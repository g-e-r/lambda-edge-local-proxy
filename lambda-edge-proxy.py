"""
MIT License

Copyright (c) 2022 Germano Leichsenring

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Emulate Lambda@Edge Viewer Request events.
S3 and Custom Origin specific parameters not supported.
"""
import base64
import fnmatch
import json
import traceback
import typing
from collections import defaultdict
from collections.abc import Set

import boto3
import botocore
import mitmproxy as mitm
import yaml
from cfn_tools import load_yaml
from mitmproxy import ctx, http

FORBIDDEN_HEADERS = [
    "connection",
    "expect",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "trailer",
    "upgrade",
    "x-accel-buffering",
    "x-accel-charset",
    "x-accel-limit-rate",
    "x-accel-redirect",
    "x-amz-cf-*",  # TODO
    "x-amzn-auth",
    "x-amzn-cf-billing",
    "x-amzn-cf-id",
    "x-amzn-cf-xff",
    "x-amzn-errotype",
    "x-amzn-fle-profile",
    "x-amzn-header-count",
    "x-amzn-lambda-integration-tag",
    "x-amzn-request-id",
    "x-cache",
    "x-edge-*",  # TODO
    "x-forwarded-proto",
    "x-real-ip",
]
READONLY_HEADERS_VIEWER_REQUESTS = [
    "content-length",
    "host",
    "transfer-encoding",
    "via",
]


def get_header_kv_capitalized(x):
    key = "-".join(w.capitalize() for w in x[0].split("-"))
    val = (
        x[1][0]["value"]
        if isinstance(x[1], list) and isinstance(x[1][0], dict) and "value" in x[1][0]
        else ""
    )
    return (x[1][0]["key"] if "key" in x[1][0] else key, val)


class LambdaEdgeLocalProxy:
    def __init__(self):
        self.endpoint = None
        self.lambda_client = None
        self.funcs = defaultdict(list)

    def load(self, loader: mitm.addonmanager.Loader):
        """Add loader options to mitmproxy.
        This function is a mitmproxy hook.
        """
        loader.add_option(
            name="lambda_at_edge_endpoint",
            typespec=str,
            default="http://127.0.0.1:3001",
            help="Lambda@Edge Endpoint URL",
        )
        loader.add_option(
            name="lambda_at_edge_cf_template",
            typespec=str,
            default="template.yaml",
            help="Lambda@Edge CloudFormation Template",
        )

    def configure(self, updates: Set[str]):
        """Configure from mitmproxy options.
        This function is a mitmproxy hook.
        """
        if "lambda_at_edge_endpoint" in updates:
            self.endpoint = ctx.options.lambda_at_edge_endpoint
            try:
                self.lambda_client = boto3.client(
                    "lambda",
                    endpoint_url=ctx.options.lambda_at_edge_endpoint,
                    use_ssl=False,
                    verify=False,
                    config=botocore.config.Config(
                        signature_version=botocore.UNSIGNED,
                        read_timeout=15,
                        retries={"max_attempts": 0},
                    ),
                )
            except Exception as e:
                ctx.log.error(e)
                traceback.print_exc(e)
        if "lambda_at_edge_cf_template" in updates:
            try:
                text = open(ctx.options.lambda_at_edge_cf_template, "r").read()
                data = load_yaml(text)
                found = None
                self.funcs = defaultdict(list)
                resources = data["Resources"]
                for k, v in resources.items():
                    if v["Type"] == "AWS::CloudFront::Distribution":
                        if found:
                            ctx.log.warn(
                                f"Lambda@Edge: only first CloudFront Distribution '{found}'"
                                + " used from the template file"
                            )
                            return
                        found = k
                        dist_config = v["Properties"]["DistributionConfig"]
                        self.populate_from_dist_config(resources, dist_config)
                if len(self.funcs) == 0:
                    ctx.log.error(
                        "Lambda@Edge: Could not find any "
                        + "LambdaFunctionAssociations in '{found}'"
                    )
            except Exception as e:
                ctx.log.error(e)
                traceback.print_exc(e)

    def populate_from_dist_config(self, resources, dist_config):
        """populate from CloudFront DistributionConfig"""
        if "CacheBehaviors" in dist_config:
            behaviors = dist_config["CacheBehaviors"]
            for behavior in behaviors:
                path = behavior["PathPattern"]
                if not isinstance(path, str):
                    ctx.log.warn("Lambda@Edge: path functions not supported")
                    continue
                if "LambdaFunctionAssociations" in behavior:
                    self.add_funcs(
                        resources, path, behavior["LambdaFunctionAssociations"]
                    )
        if "DefaultCacheBehavior" in dist_config:
            behavior = dist_config["DefaultCacheBehavior"]
            if "LambdaFunctionAssociations" in behavior:
                self.add_funcs(resources, "*", behavior["LambdaFunctionAssociations"])

    def resolve_ref(self, res, ref):
        if "Ref" in ref:
            ref_name = ref["Ref"]
            if ref_name in res:
                return ref_name
            else:
                ctx.log.error(f"Cannot resolve reference {ref} {ref_name}")
        return None

    def add_funcs(self, res, path, funcs):
        """Add template.yaml functions to self.funcs"""
        for func in funcs:
            event_type = func["EventType"] if "EventType" in func else ""
            include_body = func["IncludeBody"] if "IncludeBody" in func else False
            func_arn = func["LambdaFunctionARN"] if "LambdaFunctionARN" in func else ""
            if isinstance(func_arn, str):
                func_name = func_arn.split(":")[6]
            elif "Ref" in func_arn:
                func_version = res[self.resolve_ref(res, func_arn)]
                if func_version["Type"] != "AWS::Lambda::Version":
                    ctx.log.error(f"Lambda@Edge: not a Lambda Version reference")
                    continue
                props = (
                    func_version["Properties"] if "Properties" in func_version else {}
                )
                func_name = self.resolve_ref(res, props["FunctionName"])
            elif "Fn::GetAtt" in func_arn:
                if func_arn["Fn::GetAtt"][1] == "FunctionArn":
                    func_name = func_arn["Fn::GetAtt"][0]
                else:
                    ctx.log.error(
                        "Lambda@Edge: LambdaFunctionARN unsupported Fn::GetAtt"
                    )
                    continue
            else:
                ctx.log.warn("Lambda@Edge: LambdaFunctionARN unsupported syntax")
                continue
            if event_type == "viewer-request":
                ctx.log.info(
                    f"Lambda@Edge: viewer-request '{path}' route to '{func_name}'"
                )
            elif event_type == "origin-request":
                ctx.log.info(
                    f"Lambda@Edge: origin-request '{path}' route to '{func_name}'"
                )
            else:
                ctx.log.warn(f"Lambda@Edge: EventType '{event_type}' not supported")
            self.funcs[event_type].append((path, func_name, include_body))

    def get_client_ip(self, flow):
        """Retrieve client ip from mitmproxy flow"""
        return flow.client_conn.ip_address

    def get_headers(self, flow):
        """Tranlate mitmproxy headers to lambda@edge headers"""
        items = flow.request.headers.items()
        headers = dict(
            (x[0].lower(), [{"key": x[0], "value": x[1]}])
            for x in items
            if x[0].lower() not in FORBIDDEN_HEADERS
        )
        return headers

    def set_headers(self, flow, payload):
        """Translate lambda@edge headers to mitmproxy headers"""
        if not "headers" in payload:
            return
        headers = dict(get_header_kv_capitalized(x) for x in payload["headers"].items())
        for x in headers.keys():
            if x.lower() in FORBIDDEN_HEADERS:
                msg = f"Lambda@Edge: included forbidden header '{x}' in response"
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
        for x in headers.items():
            if x[0] not in flow.request.headers:
                if x[0].lower() in READONLY_HEADERS_VIEWER_REQUESTS:
                    msg = f"Lambda@Edge: added read-only header '{x[0]}'"
                    ctx.log.warn(msg)
                    flow.response = http.HTTPResponse.make(502, msg)
                    return
                ctx.log.info(f"Lambda@Edge: *-request added header: '{x[0]}': '{x[1]}'")
                flow.request.headers[x[0]] = x[1]
            elif flow.request.headers[x[0]] != x[1]:
                if x[0].lower() in READONLY_HEADERS_VIEWER_REQUESTS:
                    msg = (
                        f"Lambda@Edge: modified read-only header '{x[0]}' "
                        + "from '{flow.request.headers[x[0]]}' to '{x[1]}'"
                    )
                    ctx.log.warn(msg)
                    flow.response = http.HTTPResponse.make(502, msg)
                    return
                ctx.log.info(
                    f"Lambda@Edge: *-request modified header to: '{x[0]}': '{x[1]}'"
                )
                flow.request.headers[x[0]] = x[1]

    def get_method(self, flow):
        """Get HTTP method from mitmproxy"""
        return flow.request.method

    def get_body(self, flow, include_body):
        """Translate mitmproxy body to lambda@edge body"""
        if not include_body:
            # TODO - not sure what to include if there is no body
            return {
                "inputTruncated": True,
                "action": "read-only",
                "encoding": "base64",
                "data": "",
            }
        else:
            return {
                "inputTruncated": False,
                "action": "read-only",
                "encoding": "base64",
                "data": str(base64.b64encode(flow.request.content), "utf-8"),
            }

    def set_body(self, flow, payload):
        """Translate lambda@edge body to mitmproxy body"""
        if "body" not in payload:
            return
        body = payload["body"]
        if "action" not in body:
            return
        action = body["action"]
        if action == "replace":
            if body["encoding"] == "base64":
                ctx.log.info("Lambda@Edge: *-request replaced body using base64")
                new_body = base64.b64decode(body["data"])
            elif body["encoding"] == "text":
                ctx.log.info("Lambda@Edge: *-request replaced body using text")
                new_body = bytes(body["data"], "utf-8")
            else:
                msg = f"Lambda@Edge: unknown body encoding '{body['encoding']}'"
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
            flow.request.content = new_body

    def get_uri(self, flow):
        """Get URI from mitmproxy"""
        return flow.request.path.split("?")[0]

    def get_querystring(self, flow):
        """Get querystring from mitmproxy"""
        path = flow.request.path
        if "?" not in path:
            return ""
        return path[path.find("?") + 1 :]

    def set_uri(self, flow, payload):
        """Set URI from lambda@edge to mitmproxy"""
        uri = payload["uri"] if "uri" in payload else "/"
        querystring = payload["querystring"] if "querystring" in payload else ""
        if uri[0] != "/":
            msg = f"Lambda@Edge: URI must start with /"
            ctx.log.warn(msg)
            flow.response = http.HTTPResponse.make(502, msg)
            return
        if querystring != "":
            uri += "?" + querystring
        if flow.request.path != uri:
            ctx.log.info(f"Lambda@Edge: *-request replaced URI to '{uri}'")
            flow.request.path = uri

    def set_response(self, flow, payload):
        """Set mitmproxy response from lambda@edge, if needed"""
        if "status" not in payload:
            return
        content = payload["body"] if "body" in payload else ""
        encoding = payload["bodyEncoding"] if "bodyEncoding" in payload else "text"
        if encoding == "base64":
            content = str(base64.b64decode(content), "utf-8")
        headers = payload["headers"] if "headers" in payload else {}
        # TODO not sure which headers are read-only in this situation
        headers = dict(get_header_kv_capitalized(x) for x in headers.items())
        for x in headers.keys():
            if x.lower() in FORBIDDEN_HEADERS:
                msg = f"Lambda@Edge: adding forbidden header '{x}'"
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
        status_code = payload["status"]
        ctx.log.info(f"Lambda@Edge: *-request directly responded: '{status_code}'")
        flow.response = http.HTTPResponse.make(status_code, content, headers)
        if "statusDescription" in payload:
            flow.response.reason = payload["statusDescription"]

    def find_func_from_path(self, uri, event_type) -> (str, bool):
        """Find lambda@edge function from URI.
        Returns: (function name, include body)
        """
        funcs = self.funcs[event_type]
        for (pattern, func_name, include_body) in funcs:
            if fnmatch.fnmatchcase(uri, pattern):
                return (func_name, include_body)
        return (None, None)

    def request(self, flow: http.HTTPFlow):
        """Process a request from mitmproxy.
        This function is a mitmproxy hook.
        """
        uri = self.get_uri(flow)
        self.request_to_lambda(flow, uri, "viewer-request")
        if flow.response:
            return
        self.request_to_lambda(flow, uri, "origin-request")

    def response(self, flow: http.HTTPFlow):
        """Process a response from mitmproxy.
        This function is a mitmproxy hook.
        Not yet implemented - we need to keep the original uri
        in the flow structure to find the appropriate functions.
        """
        pass

    def request_to_lambda(self, flow: http.HTTPFlow, uri, event_type):
        (func_name, include_body) = self.find_func_from_path(uri, event_type)
        if func_name == None:
            return
        req = json.dumps(
            {
                "Records": [
                    {
                        "cf": {
                            "config": {
                                "distributionDomainName": "dummy.cloudfront.net",
                                "distributionId": "DUMMYIDEXAMPLE",
                                "eventType": event_type,
                                "requestId": "IsThisReallyNeeded",
                            },
                            "request": {
                                "clientIp": self.get_client_ip(flow),
                                "headers": self.get_headers(flow),
                                "method": self.get_method(flow),
                                "querystring": self.get_querystring(flow),
                                "uri": self.get_uri(flow),
                                "body": self.get_body(flow, include_body),
                            },
                        }
                    }
                ]
            }
        )
        try:
            res = self.lambda_client.invoke(FunctionName=func_name, Payload=req)
            if res["StatusCode"] != 200:
                msg = "Lambda@Edge StatusCode: " + str(res["StatusCode"])
                ctx.log.error(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
            payload_raw = res["Payload"].read()
            if not payload_raw:
                msg = f"Lambda@Edge: no payload"
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
            try:
                payload = json.loads(payload_raw)
            except json.decoder.JSONDecodeError as e:
                # If payload_raw starts with b'Task timed out after
                # then it's a timeout error - error 503
                msg = f"Lambda@Edge non-JSON payload: '{payload_raw}'"
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(503, msg)
                return
            if "FunctionError" in res:
                msg = (
                    f"Lambda@Edge FunctionError: '{res['FunctionError']}'\n'{payload}'"
                )
                ctx.log.warn(msg)
                flow.response = http.HTTPResponse.make(502, msg)
                return
            if "status" in payload:
                # Do not connect to proxy, respond directly
                self.set_response(flow, payload)
            else:
                # Overwrite headers, URI and body
                # NOTE - set_body() may change Content-Length
                # So, set_headers() must be called before set_body().
                self.set_headers(flow, payload)
                self.set_body(flow, payload)
                self.set_uri(flow, payload)
        except (
            botocore.exceptions.ReadTimeoutError,
            botocore.exceptions.ClientError,
            ConnectionRefusedError,
            botocore.exceptions.EndpointConnectionError,
        ) as e:
            msg = f"Lambda@Edge: Exception: {repr(e)}"
            ctx.log.warn(msg)
            if isinstance(e, json.decoder.JSONDecodeError):
                ctx.log.warn(payload)
            flow.response = http.HTTPResponse.make(502, msg)
        except Exception as e:
            msg = f"Lambda@Edge: Exception: {repr(e)}"
            ctx.log.error(msg)
            traceback.print_exc(e)
            flow.response = http.HTTPResponse.make(502, msg)


addons = [LambdaEdgeLocalProxy()]
