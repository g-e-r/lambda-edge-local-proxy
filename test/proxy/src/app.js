'use strict';

const querystring = require('querystring')

exports.failure = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  throw "This is a test of Lambda@Edge function call failure"
};
exports.modheader = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const headers = request.headers;
  console.log(JSON.stringify(headers))
  headers['X-Lambda-Handler'] = [{'value':'Header added by Lambda@Edge'}];
  callback(null, request);
}
exports.modbody = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  console.log(JSON.stringify(request.body))
  if (request.method === 'POST') {
    const body = Buffer.from(request.body.data, 'base64').toString();
    const params = querystring.parse(body)
    params['NewBodyParam'] = 'Body changed by Lambda@Edge'
    request.body.action = 'replace';
    request.body.encoding = 'text';
    request.body.data = querystring.stringify(params);
  }
  callback(null, request);
}
exports.respond = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const response = {
    body: 'Redirection Test',
    bodyEncoding: 'text',
    headers: {
      "x-custom-header": [{"value": "custom-value"}]
    },
    status: 200,
    statusDescription: "OK"
  }
  console.log(JSON.stringify(response))
  callback(null, response)
}
exports.moduri = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  console.log(JSON.stringify(request.uri))
  request.uri = "/dev" + request.uri;
  callback(null, request)
}

exports.success = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  console.log(JSON.stringify(request))
  callback(null, request);
};
