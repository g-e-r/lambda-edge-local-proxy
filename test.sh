#!/bin/bash
PORT=8001

# aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws

sam local start-lambda -t test/template.yaml --warm-containers EAGER &
while ! nc -z localhost 3001; do
  sleep 1
done
sam local start-api -t test/template.yaml --warm-containers EAGER &
while ! nc -z localhost 3000; do
  sleep 1
done
mitmdump -s lambda-edge-proxy.py -p $PORT -m reverse:http://localhost:3000 --set lambda_at_edge_cf_template=test/template.yaml &
while ! nc -z localhost $PORT; do
  sleep 1
done

mkdir tmp
curl -o tmp/Failure.txt -D tmp/Failure.hdr.txt localhost:$PORT/Failure/
curl -o tmp/ModHeader.txt localhost:$PORT/ModHeader/
curl -o tmp/ModBody.txt -d "data=123" localhost:$PORT/ModBody/
curl -o tmp/Respond.txt -D tmp/Respond.hdr.txt localhost:$PORT/Respond/
curl -o tmp/ModUri.txt localhost:$PORT/ModUri/
curl -o tmp/Success.txt localhost:$PORT/Success/

curl -o tmp/Failure.MALFORMED_RETURN.txt -D tmp/Failure.MALFORMED_RETURN.hdr.txt "localhost:$PORT/Failure/?p=MALFORMED_RETURN"
curl -o tmp/Failure.TIMEOUT.txt -D tmp/Failure.TIMEOUT.hdr.txt "localhost:$PORT/Failure/?p=TIMEOUT"
curl -o tmp/Failure.EXCEPTION.txt -D tmp/Failure.EXCEPTION.hdr.txt "localhost:$PORT/Failure/?p=EXCEPTION"
curl -o tmp/Failure.HEADER_NO_VALUE.txt -D tmp/Failure.HEADER_NO_VALUE.hdr.txt "localhost:$PORT/Failure/?p=HEADER_NO_VALUE"
curl -o tmp/ModHeader.VALUE_ONLY.txt "localhost:$PORT/ModHeader/?p=KV"
curl -o tmp/ModHeader.FORBIDDEN_HEADER.txt "localhost:$PORT/ModHeader/?p=KV&K=UPGRADE"
curl -o tmp/ModHeader.FORBIDDEN_HEADER_MOD.txt "localhost:$PORT/ModHeader/?K=KEEP-ALIVE"
curl -o tmp/ModHeader.READONLY_HEADER.txt "localhost:$PORT/ModHeader/?K=VIA"
curl -o tmp/ModHeader.READONLY_HEADER_MOD.txt "localhost:$PORT/ModHeader/?p=KV&K=HOST"
curl -o tmp/ModBody.BASE64.txt -d "p=base64" "localhost:$PORT/ModBody/"
curl -o tmp/ModBody.ENCODEERROR.txt -D tmp/ModBody.ENCODEERROR.hdr.txt -d "p=base32" "localhost:$PORT/ModBody/"
curl -o tmp/ModUri.NOSLASH.txt -D tmp/ModUri.NOSLASH.hdr.txt "localhost:$PORT/ModUri/?p=noslash"

for i in tmp/*.txt; do echo ---$i--- ; cat $i ; echo ; done

skill -TERM mitmdump
skill -TERM sam

# NOTE - if sam docker containers keep dangling, the following can be used to stop them.
# docker container ps | grep public.ecr.aws/sam/emulation-nodejs14.x | sed 's/.* //' | xargs docker container stop
