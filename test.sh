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
curl -o tmp/Failure.txt -D tmp/Failure.headers.txt localhost:$PORT/Failure/
curl -o tmp/ModHeader.txt localhost:$PORT/ModHeader/
curl -o tmp/ModBody.txt -d "data=123" localhost:$PORT/ModBody/
curl -o tmp/Respond.txt -D tmp/Respond.headers.txt localhost:$PORT/Respond/
curl -o tmp/ModUri.txt localhost:$PORT/ModUri/
curl -o tmp/Success.txt localhost:$PORT/Success/

for i in tmp/*; do echo ---$i--- ; cat $i ; echo ; done

skill -TERM mitmdump
skill -TERM sam

