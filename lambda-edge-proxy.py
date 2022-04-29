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
from botocore.exceptions import ReadTimeoutError, ClientError
import json
import typing
import traceback
import base64

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

    def load(self, loader: mitm.addonmanager.Loader):
        loader.add_option(
            name="lambda_at_edge_endpoint",
            typespec=str,
            default="http://127.0.0.1:3001",
            help="Lambda@Edge Endpoint URL",
        )
        loader.add_option(
            name="lambda_at_edge_viewer_request",
            typespec=typing.Optional[str],
            default=None,
            help="Lambda@Edge Viewer Request Function",
        )

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

        # TODO AWS throws 502 if read-only headers are modified
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
              ctx.log.info(f"Lambda@Edge: Header added: \"{x[0]}\": \"{x[1]}\"")
              flow.request.headers[x[0]] = x[1]
            elif flow.request.headers[x[0]] != x[1]:
              ctx.log.info(f"Lambda@Edge: Header modified: \"{x[0]}\": \"{x[1]}\"")
              flow.request.headers[x[0]] = x[1]

    def get_method(self, flow):
        return flow.request.method

    def get_body(self, flow):
        #    ctx.log.info(flow.request.content)
        return {
            "inputTruncated": False,
            "action": "read-only",
            "encoding": "base64",
            "data": str(base64.b64encode(flow.request.content), "utf-8"),
        }

    def set_body(self, flow, payload):
        if "body" not in payload: return
        body = payload["body"]
        if "action" not in body: return
        action = body["action"]
        if action == "replace":
            ctx.log.info("Lambda@Edge: replacing body")
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
            ctx.log.info(f"Lambda@Edge: replacing URI to {uri}")
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
        ctx.log.info(f"Lambda@Edge: generated response: {status_code}")
        flow.response = http.HTTPResponse.make(
            status_code=status_code, content=content, headers=headers
        )

    def request(self, flow: http.HTTPFlow):
        viewer_request = ctx.options.lambda_at_edge_viewer_request
        if viewer_request is None:
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
                                "body": self.get_body(flow),  # RW
                            },
                        }
                    }
                ]
            }
        )
#        ctx.log.info(req)
        # flow.request
        try:
            res = self.lambda_client.invoke(FunctionName=viewer_request, Payload=req)
            if res["StatusCode"] != 200:
                ctx.log.error(
                    "Lambda@Edge StatusCode: " + str(res["StatusCode"])
                )
                flow.response = http.HTTPResponse.make(500,
                  "Lambda@Edge StatusCode: " + str(res["StatusCode"])
                )
                return
            if "FunctionError" in res:
                ctx.log.error(res)
                payload = res["Payload"].read()
                payload = json.loads(payload)
                flow.response = http.HTTPResponse.make(502,
                    content="Lambda@Edge Error: " + res["FunctionError"]
                        + '\n' + str(payload) + '\n'
                )
                return
            payload = res["Payload"].read()
            payload = json.loads(payload)
#            ctx.log.info(payload)
            if "status" in payload:
                # Do not connect to proxy, respond directly
                self.set_response(flow, payload)
            else:
                # Overwrite headers, URI and body
                self.set_body(flow, payload)
                self.set_uri(flow, payload)
                self.set_headers(flow, payload)
        except (ReadTimeoutError, ClientError) as e:
            ctx.log.error(e)
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
