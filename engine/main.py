from fastapi import FastAPI, Depends
from engine.routes import router, setup_routes
from generate.models import generate_models
from pydantic import BaseModel

app = FastAPI(title="Schema Backend Engine", version="1.0.0")

# Global variables to store models
sqlalchemy_models = {}
pydantic_models = {}

@app.on_event("startup")
async def startup_event():
    global sqlalchemy_models, pydantic_models
    sqlalchemy_models, pydantic_models = generate_models()
    
    print(f"Generated models: {list(sqlalchemy_models.keys())}")
    print(f"Generated Pydantic models: {list(pydantic_models.keys())}")
    setup_routes(router, {"sqlalchemy_models": sqlalchemy_models, "pydantic_models": pydantic_models})
    print(f"Routes in router after setup: {len(router.routes)}")
    app.include_router(router)
    print(f"Routes in app after include_router: {len(app.routes)}")

def custom_openapi():
    print(f"pydantic_models in custom_openapi: {list(pydantic_models.keys())}")
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title="Schema Backend Engine",
        version="1.0.0",
        description="API for dynamic schema-based operations",
        routes=app.routes,
    )
    # Dynamically update request body schemas for POST and PUT endpoints
    for path_key, path_data in openapi_schema["paths"].items():
        for method_key, method_data in path_data.items():
            if "requestBody" in method_data and "content" in method_data["requestBody"] and "application/json" in method_data["requestBody"]["content"]:
                model_name = path_key.split("/")[1]
                if model_name in pydantic_models:
                    model = pydantic_models[model_name]
                    method_data["requestBody"]["content"]["application/json"]["schema"] = model.schema()
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi