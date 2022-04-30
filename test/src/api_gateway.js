"use strict";

exports.proxy = async (event, context) => {
  console.log(JSON.stringify(event));
  const response = {
    statusCode: 200,
    body: JSON.stringify({
      header: "user-agent = '" + event.headers["User-Agent"] + "'",
      body: event.body,
      path: event.path,
      message: "Served by API Gateway Proxy",
    }),
  };
  return response;
};
