"use strict";

const querystring = require("querystring");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

exports.failure = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const params = querystring.parse(request.querystring);
  if ("p" in params) {
    if (params["p"] === "MALFORMED_RETURN") {
      callback(null, { mal: "formed" });
    } else if (params["p"] === "TIMEOUT") {
      while (true) {
        await sleep(5 * 1000);
        console.log("Not dead yet...");
      }
    } else if (params["p"] === "HEADER_NO_VALUE") {
      callback(null, { headers: { "no-value": [{ VaLuE: "wrong" }] } });
    } else if (params["p"] === "EXCEPTION") {
      throw "Exception in Lambda @ Edge Fuction";
    }
  }
};
exports.modheader = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const params = querystring.parse(request.querystring);
  const headers = request.headers;
  const k = "K" in params ? params["K"] : "X-KeY";
  const v = "V" in params ? params["V"] : "X-VaLuE";
  if ("p" in params) {
    if (params["p"] === "KV") {
      headers[k] = [{ key: k, value: v }];
    }
  }
  headers[k] = [{ value: v }];
  console.log(JSON.stringify(headers));
  callback(null, request);
};
exports.modbody = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const params = querystring.parse(request.querystring);
  if (request.method === "POST") {
    const body = Buffer.from(request.body.data, "base64").toString();
    console.log("Mod Body: " + body);
    const params = querystring.parse(body);
    params["NewParam"] = "Body_changed_by_Lambda@Edge";
    request.body.action = "replace";
    const bodydata = querystring.stringify(params);
    if ("p" in params) {
      request.body.encoding = params["p"];
      request.body.data = Buffer.from(bodydata).toString("base64");
    } else {
      request.body.encoding = "text";
      request.body.data = bodydata;
    }
  }
  console.log(JSON.stringify(request.body));
  callback(null, request);
};
exports.respond = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  const params = querystring.parse(request.querystring);
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
  const params = querystring.parse(request.querystring);
  if ("p" in params) {
    request.uri = params["p"];
  } else {
    request.uri = "/Request_URI_Modified_by_Lambda@Edge";
  }
  console.log(JSON.stringify(request.uri));
  callback(null, request);
};

exports.success = async (event, context, callback) => {
  const request = event.Records[0].cf.request;
  console.log(JSON.stringify(request));
  callback(null, request);
};
exports.success_response = async (event, context, callback) => {
  const response = event.Records[0].cf.response;
  console.log(JSON.stringify(response));
  callback(null, response);
};
