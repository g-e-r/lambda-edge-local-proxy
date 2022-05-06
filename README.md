# Local Lambda @ Edge
 A mock version of Lambda @ Edge gateway, for local development and testing.

 It is implemented as an add-on Python script to [MITMProxy](https://mitmproxy.org) v6.
## Features
 The following Lambda calls are implemented:
 - viewer request calls
 - origin request calls

When there is a request to MITM Proxy (e.g. http://localhost:8001/ )
- The  [lambda-edge-proxy.py](lambda-edge-proxy.py) script sends a request to the Lambda@Edge endpoint (e.g. http://localhost:3001/ ) based on the YAML configuration
- The Lambda@Edge endpoint has an opportunity to change headers, URI, body, or directly send a response
- If the Lambda@Edge does not send a response, then MITM Proxy will send the updated request to the actual server (e.g. http://localhost:3000/ ).

## Usage
### 1. Create a configuration file (YAML template)
A configuration file (similar to a CloudFormation template) is used to setup.
The following is a minimalistic example which uses <b>src/lambda.handler</b> as a viewer request gateway proxy.
```yaml
Resources:
  HandlerFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: src/lambda.handler
      CodeUri: ./
  CloudFrontDistribution:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        DefaultCacheBehavior:
          LambdaFunctionAssociations:
            - EventType: viewer-request
              LambdaFunctionARN: !GetAtt HandlerFunction.FunctionArn
```
### 2. Start MITM Proxy
After creating the configuration file, start MITM Proxy:
```sh
mitmproxy -s lambda-edge-proxy.py -p 8001 -m reverse:http://localhost:3000 \
  --set lambda_at_edge_cf_template=template.yaml \
  --set lambda_at_edge_endpoint=http://localhost:3001
```
## Working Example
Please have a look at [test.sh](test.sh) for a working example that starts sam local APIs before the MITM Proxy and runs simple tests afterwards.

 The testing scripts assume that CloudFront will be redirecting the calls to a local API Gateway.
 If that's not the case, you can adjust mitmdump parameters to call the actual server that will be behind CloudFront.

1. Start a local endpoint to invoke Lambda@Edge Lambda APIs.
```sh
sam local start-lambda -t test/template-simple.yaml --warm-containers EAGER &
```
2. Setup a local endpoint for API Gateway
```sh
sam local start-api -t test/template-simple.yaml --warm-containers EAGER &
```
3. Start the MITM Proxy on port 8000
```sh
mitmdump -s lambda-edge-proxy.py -p 8001 -m reverse:http://localhost:3000 --set lambda_at_edge_cf_template=test/template-setup.yaml &
```
mitmdump options:
 - <b>-p</b>: Port to listen to
 - <b>-m</b>: Reverse proxy setup (using localhost:3000 to connect to sam local API)

script options:
 - <b>lambda_at_edge_cf_template</b> : template.yaml to use
 - <b>lambda_at_edge_endpoint</b> : endpoint for Lambda@Edge function calls (default: localhost:3001 to connect to sam local Lambda)

 Please refer to [test.sh](test.sh) and [test/template-simple.yaml](test/template-simple.yaml) for more details.

## Dependencies
 - Python 3.9.2
 - mitmproxy 6.0.2
 - boto3              1.21.40
 - cfn-tools          0.1.6
 - boto3              1.21.40
 - botocore           1.24.40

## Caveats
Only some of the error cases are implemented.

Only limited parsing of CloudFormation template YAML files is implemented.
[test/template-simple.yaml](test/template-simple.yaml) (a simplified template) or [test/template.yaml](test/template.yaml) (CloudFormation working template) can be used as a starting point.

# License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
