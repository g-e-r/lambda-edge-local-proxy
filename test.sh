#!/bin/bash
PORT=8001
SERVER=http://localhost:$PORT

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
curl -o tmp/Failure.txt -D tmp/Failure.hdr.txt $SERVER/Failure/
curl -o tmp/ModHeader.txt $SERVER/ModHeader/
curl -o tmp/ModBody.txt -d "data=123" $SERVER/ModBody/
curl -o tmp/Respond.txt -D tmp/Respond.hdr.txt $SERVER/Respond/
curl -o tmp/ModUri.txt $SERVER/ModUri/
curl -o tmp/Success.txt $SERVER/Success/

curl -o tmp/Failure.EMPTY_RETURN.txt -D tmp/Failure.MALFORMED_RETURN.hdr.txt "$SERVER/Failure/?p=EMPTY_RETURN"
curl -o tmp/Failure.HEADER_KV_MISMATCH.txt "$SERVER/Failure/?p=HEADER_KV_MISMATCH"
curl -o tmp/Failure.TIMEOUT.txt -D tmp/Failure.TIMEOUT.hdr.txt "$SERVER/Failure/?p=TIMEOUT"
curl -o tmp/Failure.EXCEPTION.txt -D tmp/Failure.EXCEPTION.hdr.txt "$SERVER/Failure/?p=EXCEPTION"
curl -o tmp/Failure.HEADER_NO_VALUE.txt -D tmp/Failure.HEADER_NO_VALUE.hdr.txt "$SERVER/Failure/?p=HEADER_NO_VALUE"
curl -o tmp/ModHeader.KV.txt "$SERVER/ModHeader/?p=KV"
curl -o tmp/ModHeader.FORBIDDEN_HEADER.txt "$SERVER/ModHeader/?p=KV&K=UPGRADE"
curl -o tmp/ModHeader.FORBIDDEN_HEADER_MOD.txt "$SERVER/ModHeader/?K=KEEP-ALIVE"
curl -o tmp/ModHeader.READONLY_HEADER.txt "$SERVER/ModHeader/?K=VIA"
curl -o tmp/ModHeader.READONLY_HEADER_MOD.txt "$SERVER/ModHeader/?p=KV&K=HOST"
curl -o tmp/ModHeader.READONLY_HEADER_DEL.txt "$SERVER/ModHeader/?p=DEL&K=HOST"
curl -o tmp/ModBody.BASE64.txt -d "p=base64" "$SERVER/ModBody/"
curl -o tmp/ModBody.ENCODEERROR.txt -D tmp/ModBody.ENCODEERROR.hdr.txt -d "p=base32" "$SERVER/ModBody/"
curl -o tmp/ModUri.NOSLASH.txt -D tmp/ModUri.NOSLASH.hdr.txt "$SERVER/ModUri/?p=noslash"

for i in tmp/*.txt; do echo ---$i--- ; cat $i ; echo ; done

skill -TERM mitmdump
skill -TERM sam

# NOTE - if sam docker containers keep dangling, the following can be used to stop them.
# docker container ps | grep public.ecr.aws/sam/emulation-nodejs14.x | sed 's/.* //' | xargs docker container stop
