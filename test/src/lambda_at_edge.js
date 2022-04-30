"use strict";

const querystring = require("querystring");

exports.failure = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  throw "This is a test of Lambda@Edge function call failure";
};
exports.modheader = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const headers = request.headers;
  headers["x-lambda-handler"] = [{ value: "Header added by Lambda@Edge" }];
  console.log(JSON.stringify(headers));
  callback(null, request);
};
exports.modbody = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  if (request.method === "POST") {
    const body = Buffer.from(request.body.data, "base64").toString();
    console.log("Mod Body: " + body);
    const params = querystring.parse(body);
    params["NewParam"] = "Body_changed_by_Lambda@Edge";
    request.body.action = "replace";
    request.body.encoding = "text";
    request.body.data = querystring.stringify(params);
  }
  console.log(JSON.stringify(request.body));
  callback(null, request);
};
exports.respond = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const response = {
    body: '{"message":"Served_by_Lambda@Edge"}',
    bodyEncoding: "text",
    headers: {
      "content-type": [{ value: "application/json" }],
      "x-lambda-handler": [{ value: "Header added by Lambda@Edge" }],
    },
    status: 202,
    statusDescription: "Accepted Allright",
  };
  console.log(JSON.stringify(response));
  callback(null, response);
};
exports.moduri = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  request.uri = "/Request_URI_Modified_by_Lambda@Edge";
  console.log(JSON.stringify(request.uri));
  callback(null, request);
};

exports.success = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  console.log(JSON.stringify(request));
  callback(null, request);
};
