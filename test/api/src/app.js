let response;

exports.lambdaHandler = async (event, context) => {
    console.log(JSON.stringify(event))
    const response = {
        'statusCode': 200,
        'body': JSON.stringify({
            message: 'hello world',
        })
    }
    return response
};
