from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "argus_devops_2026"
    neo4j_database: str = "neo4j"
    argus_env: str = "development"
    app_name: str = "Argus"
    app_version: str = "0.1.0"
    github_webhook_secret: str = ""
    k8s_watcher_enabled: bool = False
    k8s_watcher_namespace: str = ""
    k8s_watcher_interval_seconds: int = 30
    k8s_kubeconfig_path: str = ""
    argus_log_level: str = "INFO"

    model_config = {"env_prefix": "argus_"}


settings = Settings()
