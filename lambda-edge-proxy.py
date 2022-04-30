"""
  Emulate Lambda@Edge Viewer Request events.
  S3 and Custom Origin specific parameters not supported.
"""
from mitmproxy import ctx, http
import mitmproxy as mitm
from collections.abc import Set
import boto3
from botocore.config import Config
from botocore import UNSIGNED
from botocore.exceptions import ReadTimeoutError, ClientError, EndpointConnectionError
import json
import typing
import traceback
import base64
from collections import defaultdict
import yaml
from cfn_tools import load_yaml
import fnmatch

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


class LambdaEdgeLocalProxy:
    def __init__(self):
        self.endpoint = None
        self.lambda_client = None
        self.funcs = defaultdict(list)

    def load(self, loader: mitm.addonmanager.Loader):
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

    def add_funcs(self, path, funcs):
        for func in funcs:
            event_type = func["EventType"] if "EventType" in func else ""
            include_body = func["IncludeBody"] if "IncludeBody" in func else False
            func_arn = func["LambdaFunctionARN"] if "LambdaFunctionARN" in func else ""
            if isinstance(func_arn, str):
                func_name = func_arn.split(":")[6]
            elif "Fn::GetAtt" in func_arn:
                if func_arn["Fn::GetAtt"][1] == "FunctionArn":
                    func_name = func_arn["Fn::GetAtt"][0]
                else:
                    ctx.log.error(
                        "Lambda@Edge: LambdaFunctionARN unsupported Fn::GetAtt"
                    )
                    continue
            else:
                ctx.log.warn("Lambda@Edge: LambdaFunctionARN unsupported Fn::")
                continue
            if event_type == "viewer-request":
                ctx.log.info(
                    f"Lambda@Edge: viewer-request '{path}' route  to '{func_name}'"
                )
                self.funcs[event_type].append((path, func_name, include_body))
            else:
                ctx.log.warn(f"Lambda@Edge: EventType {event_type} not supported")

    def configure(self, updates: Set[str]):
        if "lambda_at_edge_endpoint" in updates:
            self.endpoint = ctx.options.lambda_at_edge_endpoint
            try:
                self.lambda_client = boto3.client(
                    "lambda",
                    endpoint_url=ctx.options.lambda_at_edge_endpoint,
                    use_ssl=False,
                    verify=False,
                    config=Config(
                        signature_version=UNSIGNED,
                        read_timeout=15,
                        retries={"max_attempts": 0},
                    ),
                )
            except Exception as e:
                ctx.log.error(e)
        if "lambda_at_edge_cf_template" in updates:
            try:
                text = open(ctx.options.lambda_at_edge_cf_template, "r").read()
                data = load_yaml(text)
                found = False
                self.funcs = defaultdict(list)
                for k, v in data["Resources"].items():
                    if v["Type"] == "AWS::CloudFront::Distribution":
                        if found:
                            ctx.log.warn(
                                "Lambda@Edge: only first CloudFront Distribution "
                                + "is used in the template file"
                            )
                            return
                        found = True
                        dist_config = v["Properties"]["DistributionConfig"]
                        self.populate_from_dist_config(dist_config)
                if len(self.funcs) == 0:
                    ctx.log.error(
                        "Lambda@Edge: Could not find any LambdaFunctionAssociations"
                    )
            except Exception as e:
                ctx.log.error(e)

    def populate_from_dist_config(self, dist_config):
        if "CacheBehaviors" in dist_config:
            behaviors = dist_config["CacheBehaviors"]
            for behavior in behaviors:
                path = behavior["PathPattern"]
                if not isinstance(path, str):
                    ctx.log.warn("Lambda@Edge: path functions not supported")
                    continue
                if "LambdaFunctionAssociations" in behavior:
                    self.add_funcs(path, behavior["LambdaFunctionAssociations"])
        if "DefaultCacheBehavior" in dist_config:
            behavior = dist_config["DefaultCacheBehavior"]
            if "LambdaFunctionAssociations" in behavior:
                self.add_funcs("*", behavior["LambdaFunctionAssociations"])

    def get_client_ip(self, flow):
        return flow.client_conn.ip_address

    def get_headers(self, flow):
        items = flow.request.headers.items()
        headers = dict(
            (x[0].lower(), [{"key": x[0], "value": x[1]}])
            for x in items
            if x[0].lower() not in FORBIDDEN_HEADERS
        )
        return headers

    def set_headers(self, flow, payload):
        # TODO fix Camel-Case of x[0]
        def get_header_pair(x):
            return (x[1][0]["key"] if "key" in x[1][0] else x[0], x[1][0]["value"])

        # TODO must throw 502 if read-only headers are modifed
        headers = dict(
            get_header_pair(x)
            for x in payload["headers"].items()
            if x[0].lower() not in READONLY_HEADERS_VIEWER_REQUESTS
        )
        for x in headers.keys():
            if x.lower() in FORBIDDEN_HEADERS:
                flow.response = http.HTTPResponse.make(502)
                return
        for x in headers.items():
            if x[0] not in flow.request.headers:
                ctx.log.info(
                    f'Lambda@Edge: viewer-request added header: "{x[0]}": "{x[1]}"'
                )
                flow.request.headers[x[0]] = x[1]
            elif flow.request.headers[x[0]] != x[1]:
                ctx.log.info(
                    f'Lambda@Edge: viewer-request modified header to: "{x[0]}": "{x[1]}"'
                )
                flow.request.headers[x[0]] = x[1]

    def get_method(self, flow):
        return flow.request.method

    def get_body(self, flow, include_body):
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
        if "body" not in payload:
            return
        body = payload["body"]
        if "action" not in body:
            return
        action = body["action"]
        if action == "replace":
            ctx.log.info("Lambda@Edge: viewer-request replaced body")
            if body["encoding"] == "base64":
                new_body = str(base64.b64decode(body["data"]), "utf-8")
            elif body["encoding"] == "text":
                new_body = body["data"]
            else:
                ctx.log.error(payload)
                flow.response = http.HTTPResponse.make(502)

    def get_uri(self, flow):
        return flow.request.path.split("?")[0]

    def get_querystring(self, flow):
        path = flow.request.path
        if "?" not in path:
            return ""
        return path[path.find("?") + 1 :]

    def set_uri(self, flow, payload):
        uri = payload["uri"]
        if uri[0] != "/":
            ctx.log.error(payload)
            flow.response = http.HTTPResponse.make(502)
            return
        if payload["querystring"] != "":
            uri += "?" + payload["querystring"]
        if flow.request.path != uri:
            ctx.log.info(f"Lambda@Edge: viewer-request replaced URI to '{uri}'")
            flow.request.path = uri

    def set_response(self, flow, payload):
        if "status" not in payload:
            return
        content = payload["body"] if "body" in payload else ""
        encoding = payload["bodyEncoding"] if "bodyEncoding" in payload else "text"
        if encoding == "base64":
            content = str(base64.b64decode(content), "utf-8")
        headers = payload["headers"] if "headers" in payload else {}
        # TODO fix Camel-Case of x[0]
        def get_header_pair(x):
            return (x[1][0]["key"] if "key" in x[1][0] else x[0], x[1][0]["value"])

        # TODO not sure which headers are read-only in this situation
        headers = dict(get_header_pair(x) for x in headers.items())
        for x in headers.keys():
            if x in FORBIDDEN_HEADERS:
                flow.response = http.HTTPResponse.make(502)
                return
        status_code = payload["status"]
        ctx.log.info(f"Lambda@Edge: viewer-request directly responded: {status_code}")
        flow.response = http.HTTPResponse.make(
            status_code=status_code, content=content, headers=headers
        )

    def find_func_from_path(self, uri, event_type):
        funcs = self.funcs[event_type]
        for (pattern, func_name, include_body) in funcs:
            # We assume that AWS path pattern can be used as-is in fnmatch()
            if fnmatch.fnmatch(uri, pattern):
                return (func_name, include_body)
        return (None, None)

    def request(self, flow: http.HTTPFlow):
        uri = self.get_uri(flow)
        (func_name, include_body) = self.find_func_from_path(uri, "viewer-request")
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
                                "eventType": "viewer-request",
                                "requestId": "IsThisReallyNeeded",
                            },
                            "request": {
                                "clientIp": self.get_client_ip(flow),  # RO
                                "headers": self.get_headers(flow),  # RW
                                "method": self.get_method(flow),  # RO
                                "querystring": self.get_querystring(flow),  # RW
                                "uri": self.get_uri(flow),  # RW
                                "body": self.get_body(flow, include_body),  # RW
                            },
                        }
                    }
                ]
            }
        )
        try:
            res = self.lambda_client.invoke(FunctionName=func_name, Payload=req)
            if res["StatusCode"] != 200:
                ctx.log.error("Lambda@Edge StatusCode: " + str(res["StatusCode"]))
                flow.response = http.HTTPResponse.make(
                    500, "Lambda@Edge StatusCode: " + str(res["StatusCode"])
                )
                return
            if "FunctionError" in res:
                ctx.log.error(res)
                payload = res["Payload"].read()
                payload = json.loads(payload)
                flow.response = http.HTTPResponse.make(
                    502,
                    content="Lambda@Edge Error: "
                    + res["FunctionError"]
                    + "\n"
                    + str(payload)
                    + "\n",
                )
                return
            payload = res["Payload"].read()
            payload = json.loads(payload)
            if "status" in payload:
                # Do not connect to proxy, respond directly
                self.set_response(flow, payload)
            else:
                # Overwrite headers, URI and body
                self.set_body(flow, payload)
                self.set_uri(flow, payload)
                self.set_headers(flow, payload)
        except (ReadTimeoutError, ClientError, ConnectionRefusedError, EndpointConnectionError) as e:
            ctx.log.warn(e)
            flow.response = http.HTTPResponse.make(
                status_code=502,
                content="Exception: " + repr(e),
                headers={"Content-Type": "text/plain"},
            )
        except Exception as e:
            ctx.log.error(e)
            traceback.print_exc(e)
            flow.response = http.HTTPResponse.make(
                status_code=502,
                content="Exception: " + repr(e),
                headers={"Content-Type": "text/plain"},
            )


addons = [LambdaEdgeLocalProxy()]
