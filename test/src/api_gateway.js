"use strict";

exports.proxy = async (event, context) => {
  console.log(JSON.stringify(event));
  const response = {
    statusCode: 200,
    body: JSON.stringify({
      header: event.headers["X-Lambda-Handler"],
      body: event.body,
      path: event.path,
      message: "Served by API Gateway Proxy",
    }),
  };
  return response;
};
