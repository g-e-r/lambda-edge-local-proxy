#!/bin/bash
PORT=8001

sam local start-lambda -t test/proxy/template.yaml --warm-containers EAGER --skip-pull-image &
sam local start-api -t test/api/template.yaml --warm-containers EAGER --skip-pull-image &
mitmdump -s lambda-edge-proxy.py -p $PORT -m reverse:http://localhost:3000 --set lambda_at_edge_viewer_request=ModUri &
sleep 5
curl localhost:$PORT/HelloWorld


