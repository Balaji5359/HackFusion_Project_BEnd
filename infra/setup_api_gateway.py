import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
API_NAME = "AgenticPharmacyHTTPAPI"
ROUTES = {
    "GET /medicine": "get_medicine_details",
    "GET /medicines": "list_medicines",
    "GET /orders": "list_orders",
    "POST /order": "place_order_atomic",
}

apigw = boto3.client("apigatewayv2", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)
account_id = sts.get_caller_identity()["Account"]


def get_or_create_api():
    apis = apigw.get_apis().get("Items", [])
    for api in apis:
        if api.get("Name") == API_NAME and api.get("ProtocolType") == "HTTP":
            return api["ApiId"], api["ApiEndpoint"]

    resp = apigw.create_api(
        Name=API_NAME,
        ProtocolType="HTTP",
        CorsConfiguration={
            "AllowOrigins": ["*"],
            "AllowMethods": ["GET", "POST", "OPTIONS"],
            "AllowHeaders": ["*"],
            "ExposeHeaders": ["*"],
            "MaxAge": 86400,
        },
    )
    return resp["ApiId"], resp["ApiEndpoint"]


def get_lambda_arn(name):
    return lambda_client.get_function(FunctionName=name)["Configuration"]["FunctionArn"]


def upsert_integration(api_id, lambda_arn):
    integrations = apigw.get_integrations(ApiId=api_id).get("Items", [])
    integration_uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
    for i in integrations:
        if i.get("IntegrationUri") == integration_uri:
            return i["IntegrationId"]

    r = apigw.create_integration(
        ApiId=api_id,
        IntegrationType="AWS_PROXY",
        IntegrationMethod="POST",
        IntegrationUri=integration_uri,
        PayloadFormatVersion="2.0",
        TimeoutInMillis=30000,
    )
    return r["IntegrationId"]


def upsert_route(api_id, route_key, integration_id):
    routes = apigw.get_routes(ApiId=api_id).get("Items", [])
    target = f"integrations/{integration_id}"

    for r in routes:
        if r.get("RouteKey") == route_key:
            apigw.update_route(ApiId=api_id, RouteId=r["RouteId"], Target=target)
            return r["RouteId"]

    return apigw.create_route(ApiId=api_id, RouteKey=route_key, Target=target)["RouteId"]


def ensure_stage(api_id):
    stages = apigw.get_stages(ApiId=api_id).get("Items", [])
    for s in stages:
        if s.get("StageName") == "$default":
            return
    apigw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)


def add_lambda_permission(api_id, fn_name):
    statement_id = f"AllowApiGatewayInvoke-{api_id}-{fn_name}"[:100]
    source_arn = f"arn:aws:execute-api:{REGION}:{account_id}:{api_id}/*/*"
    try:
        lambda_client.add_permission(
            FunctionName=fn_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise


def main():
    api_id, endpoint = get_or_create_api()

    for route_key, fn_name in ROUTES.items():
        lambda_arn = get_lambda_arn(fn_name)
        integration_id = upsert_integration(api_id, lambda_arn)
        upsert_route(api_id, route_key, integration_id)
        add_lambda_permission(api_id, fn_name)

    ensure_stage(api_id)

    print(f"API ID: {api_id}")
    print(f"Base URL: {endpoint}")
    print(f"GET medicine: {endpoint}/medicine?medicine_name=Crocin")
    print(f"GET medicines: {endpoint}/medicines")
    print(f"GET orders: {endpoint}/orders")
    print(f"POST order: {endpoint}/order")


if __name__ == "__main__":
    main()
