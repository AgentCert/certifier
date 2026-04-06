"""Available tools registry for ITOps agent trace generation.

Mirrors the tool definitions from available_tools.md (Kubernetes + Prometheus).
"""

AVAILABLE_TOOLS = {
    # Kubernetes tools
    "k8s_config_view": {
        "name": "Configuration: View",
        "description": "Get the current Kubernetes configuration content as a kubeconfig YAML",
        "category": "kubernetes",
    },
    "k8s_events_list": {
        "name": "Events: List",
        "description": "List Kubernetes events (warnings, errors, state changes) for debugging and troubleshooting",
        "category": "kubernetes",
    },
    "k8s_namespaces_list": {
        "name": "Namespaces: List",
        "description": "List all Kubernetes namespaces in the current cluster",
        "category": "kubernetes",
    },
    "k8s_node_log": {
        "name": "Node: Log",
        "description": "Get logs from a Kubernetes node (kubelet, kube-proxy, or other system logs)",
        "category": "kubernetes",
    },
    "k8s_node_stats": {
        "name": "Node: Stats Summary",
        "description": "Get detailed resource usage statistics from a Kubernetes node via kubelet Summary API",
        "category": "kubernetes",
    },
    "k8s_nodes_top": {
        "name": "Nodes: Top",
        "description": "List the resource consumption (CPU and memory) for Kubernetes Nodes",
        "category": "kubernetes",
    },
    "k8s_pods_delete": {
        "name": "Pods: Delete",
        "description": "Delete a Kubernetes Pod in the provided namespace",
        "category": "kubernetes",
    },
    "k8s_pods_exec": {
        "name": "Pods: Exec",
        "description": "Execute a command in a Kubernetes Pod (shell access, run commands in container)",
        "category": "kubernetes",
    },
    "k8s_pods_get": {
        "name": "Pods: Get",
        "description": "Get a Kubernetes Pod in the provided namespace with the provided name",
        "category": "kubernetes",
    },
    "k8s_pods_list": {
        "name": "Pods: List",
        "description": "List all Kubernetes pods from all namespaces",
        "category": "kubernetes",
    },
    "k8s_pods_list_ns": {
        "name": "Pods: List in Namespace",
        "description": "List all Kubernetes pods in the specified namespace",
        "category": "kubernetes",
    },
    "k8s_pods_log": {
        "name": "Pods: Log",
        "description": "Get the logs of a Kubernetes Pod",
        "category": "kubernetes",
    },
    "k8s_pods_run": {
        "name": "Pods: Run",
        "description": "Run a Kubernetes Pod with the provided container image",
        "category": "kubernetes",
    },
    "k8s_pods_top": {
        "name": "Pods: Top",
        "description": "List the resource consumption (CPU and memory) for Kubernetes Pods",
        "category": "kubernetes",
    },
    "k8s_resources_create_update": {
        "name": "Resources: Create or Update",
        "description": "Create or update a Kubernetes resource by providing YAML or JSON",
        "category": "kubernetes",
    },
    "k8s_resources_delete": {
        "name": "Resources: Delete",
        "description": "Delete a Kubernetes resource by apiVersion, kind, namespace, and name",
        "category": "kubernetes",
    },
    "k8s_resources_get": {
        "name": "Resources: Get",
        "description": "Get a Kubernetes resource by apiVersion, kind, namespace, and name",
        "category": "kubernetes",
    },
    "k8s_resources_list": {
        "name": "Resources: List",
        "description": "List Kubernetes resources by apiVersion and kind",
        "category": "kubernetes",
    },
    "k8s_resources_scale": {
        "name": "Resources: Scale",
        "description": "Get or update the scale of a Kubernetes resource",
        "category": "kubernetes",
    },
    # Prometheus tools
    "prom_health_check": {
        "name": "Health Check",
        "description": "Health check endpoint for container monitoring and status verification",
        "category": "prometheus",
    },
    "prom_query": {
        "name": "Execute PromQL Query",
        "description": "Execute a PromQL instant query against Prometheus",
        "category": "prometheus",
    },
    "prom_range_query": {
        "name": "Execute PromQL Range Query",
        "description": "Execute a PromQL range query with start/end time and step interval",
        "category": "prometheus",
    },
    "prom_list_metrics": {
        "name": "List Available Metrics",
        "description": "List all available metrics in Prometheus with optional pagination",
        "category": "prometheus",
    },
    "prom_metric_metadata": {
        "name": "Get Metric Metadata",
        "description": "Get metadata (type, help, unit) for metrics",
        "category": "prometheus",
    },
    "prom_scrape_targets": {
        "name": "Get Scrape Targets",
        "description": "Get information about all scrape targets",
        "category": "prometheus",
    },
}
