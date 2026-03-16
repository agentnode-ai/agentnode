"""Kubernetes cluster management tool using the official kubernetes-client."""

from __future__ import annotations

from kubernetes import client, config
from kubernetes.client.rest import ApiException


def _load_config(**kwargs) -> None:
    """Load kube config from file or in-cluster environment."""
    kubeconfig = kwargs.get("kubeconfig")
    context = kwargs.get("context")
    try:
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig, context=context)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config(context=context)
    except Exception as exc:
        raise RuntimeError(f"Failed to load Kubernetes config: {exc}") from exc


def _pod_to_dict(pod) -> dict:
    """Serialise a V1Pod to a plain dict."""
    status = pod.status
    container_statuses = []
    for cs in (status.container_statuses or []):
        container_statuses.append({
            "name": cs.name,
            "ready": cs.ready,
            "restart_count": cs.restart_count,
            "image": cs.image,
        })
    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": status.phase,
        "pod_ip": status.pod_ip,
        "node_name": pod.spec.node_name,
        "containers": container_statuses,
        "created": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
    }


def _deployment_to_dict(dep) -> dict:
    """Serialise a V1Deployment to a plain dict."""
    spec = dep.spec
    status = dep.status
    return {
        "name": dep.metadata.name,
        "namespace": dep.metadata.namespace,
        "replicas": spec.replicas,
        "ready_replicas": status.ready_replicas or 0,
        "available_replicas": status.available_replicas or 0,
        "updated_replicas": status.updated_replicas or 0,
        "created": dep.metadata.creation_timestamp.isoformat() if dep.metadata.creation_timestamp else None,
    }


def _service_to_dict(svc) -> dict:
    """Serialise a V1Service to a plain dict."""
    spec = svc.spec
    ports = []
    for p in (spec.ports or []):
        ports.append({
            "name": p.name,
            "port": p.port,
            "target_port": str(p.target_port),
            "protocol": p.protocol,
            "node_port": p.node_port,
        })
    return {
        "name": svc.metadata.name,
        "namespace": svc.metadata.namespace,
        "type": spec.type,
        "cluster_ip": spec.cluster_ip,
        "ports": ports,
        "selector": spec.selector,
        "created": svc.metadata.creation_timestamp.isoformat() if svc.metadata.creation_timestamp else None,
    }


def run(operation: str, namespace: str = "default", **kwargs) -> dict:
    """Manage Kubernetes resources.

    Parameters
    ----------
    operation : str
        One of: list_pods, list_deployments, get_pod_logs, scale, list_services.
    namespace : str
        Kubernetes namespace (default ``"default"``).
    **kwargs :
        pod_name : str – required for get_pod_logs.
        tail : int – number of log lines (default 100).
        deployment : str – required for scale.
        replicas : int – required for scale.
        kubeconfig : str – optional path to kubeconfig file.
        context : str – optional kubeconfig context.

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    try:
        _load_config(**kwargs)
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()

    try:
        if operation == "list_pods":
            pods = v1.list_namespaced_pod(namespace=namespace)
            return {
                "status": "ok",
                "namespace": namespace,
                "pods": [_pod_to_dict(p) for p in pods.items],
                "count": len(pods.items),
            }

        elif operation == "list_deployments":
            deps = apps_v1.list_namespaced_deployment(namespace=namespace)
            return {
                "status": "ok",
                "namespace": namespace,
                "deployments": [_deployment_to_dict(d) for d in deps.items],
                "count": len(deps.items),
            }

        elif operation == "get_pod_logs":
            pod_name = kwargs["pod_name"]
            tail = kwargs.get("tail", 100)
            container = kwargs.get("container")
            log_kwargs: dict = {
                "name": pod_name,
                "namespace": namespace,
                "tail_lines": tail,
            }
            if container:
                log_kwargs["container"] = container
            logs = v1.read_namespaced_pod_log(**log_kwargs)
            lines = logs.strip().splitlines() if logs else []
            return {
                "status": "ok",
                "pod_name": pod_name,
                "namespace": namespace,
                "lines": lines,
                "line_count": len(lines),
            }

        elif operation == "scale":
            deployment = kwargs["deployment"]
            replicas = int(kwargs["replicas"])
            body = {"spec": {"replicas": replicas}}
            result = apps_v1.patch_namespaced_deployment_scale(
                name=deployment,
                namespace=namespace,
                body=body,
            )
            return {
                "status": "ok",
                "deployment": deployment,
                "namespace": namespace,
                "replicas": replicas,
                "message": f"Scaled {deployment} to {replicas} replicas",
            }

        elif operation == "list_services":
            services = v1.list_namespaced_service(namespace=namespace)
            return {
                "status": "ok",
                "namespace": namespace,
                "services": [_service_to_dict(s) for s in services.items],
                "count": len(services.items),
            }

        else:
            return {
                "status": "error",
                "message": f"Unknown operation: {operation}. "
                "Valid operations: list_pods, list_deployments, get_pod_logs, scale, list_services",
            }

    except KeyError as exc:
        return {"status": "error", "message": f"Missing required parameter: {exc}"}
    except ApiException as exc:
        return {"status": "error", "message": f"Kubernetes API error ({exc.status}): {exc.reason}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
