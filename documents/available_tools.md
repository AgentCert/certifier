
# Available Tools

## Kubernetes Tools

| Tool Name | Description |
|-----------|-------------|
| Configuration: View | Get the current Kubernetes configuration content as a kubeconfig YAML |
| Events: List | List Kubernetes events (warnings, errors, state changes) for debugging and troubleshooting in the current cluster from all namespaces |
| Namespaces: List | List all the Kubernetes namespaces in the current cluster |
| Node: Log | Get logs from a Kubernetes node (kubelet, kube-proxy, or other system logs). This accesses node logs through the Kubernetes API proxy to the kubelet |
| Node: Stats Summary | Get detailed resource usage statistics from a Kubernetes node via the kubelet's Summary API. Provides comprehensive metrics including CPU, memory, filesystem, and network usage at the node, pod, and container levels. On systems with cgroup v2 and kernel 4.20+, also includes PSI (Pressure Stall Information) metrics that show resource pressure for CPU, memory, and I/O. |
| Nodes: Top | List the resource consumption (CPU and memory) as recorded by the Kubernetes Metrics Server for the specified Kubernetes Nodes or all nodes in the cluster |
| Pods: Delete | Delete a Kubernetes Pod in the current or provided namespace with the provided name |
| Pods: Exec | Execute a command in a Kubernetes Pod (shell access, run commands in container) in the current or provided namespace with the provided name and command |
| Pods: Get | Get a Kubernetes Pod in the current or provided namespace with the provided name |
| Pods: List | List all the Kubernetes pods in the current cluster from all namespaces |
| Pods: List in Namespace | List all the Kubernetes pods in the specified namespace in the current cluster |
| Pods: Log | Get the logs of a Kubernetes Pod in the current or provided namespace with the provided name |
| Pods: Run | Run a Kubernetes Pod in the current or provided namespace with the provided container image and optional name |
| Pods: Top | List the resource consumption (CPU and memory) as recorded by the Kubernetes Metrics Server for the specified Kubernetes Pods in the all namespaces, the provided namespace, or the current namespace |
| Resources: Create or Update | Create or update a Kubernetes resource in the current cluster by providing a YAML or JSON representation of the resource (common apiVersion and kind include: v1 Pod, v1 Service, v1 Node, apps/v1 Deployment, networking.k8s.io/v1 Ingress) |
| Resources: Delete | Delete a Kubernetes resource in the current cluster by providing its apiVersion, kind, optionally the namespace, and its name (common apiVersion and kind include: v1 Pod, v1 Service, v1 Node, apps/v1 Deployment, networking.k8s.io/v1 Ingress) |
| Resources: Get | Get a Kubernetes resource in the current cluster by providing its apiVersion, kind, optionally the namespace, and its name (common apiVersion and kind include: v1 Pod, v1 Service, v1 Node, apps/v1 Deployment, networking.k8s.io/v1 Ingress) |
| Resources: List | List Kubernetes resources and objects in the current cluster by providing their apiVersion and kind and optionally the namespace and label selector (common apiVersion and kind include: v1 Pod, v1 Service, v1 Node, apps/v1 Deployment, networking.k8s.io/v1 Ingress) |
| Resources: Scale | Get or update the scale of a Kubernetes resource in the current cluster by providing its apiVersion, kind, name, and optionally the namespace. If the scale is set in the tool call, the scale will be updated to that value. Always returns the current scale of the resource |

## Prometheus Tools

| Tool Name | Description |
|-----------|-------------|
| Health Check | Health check endpoint for container monitoring and status verification |
| Execute PromQL Query | Execute a PromQL instant query against Prometheus |
| Execute PromQL Range Query | Execute a PromQL range query with start time, end time, and step interval |
| List Available Metrics | List all available metrics in Prometheus with optional pagination support |
| Get Metric Metadata | Get metadata (type, help, unit) for metrics. Returns all metric metadata when no metric name is provided. Use filter_pattern to search metric names and descriptions. |
| Get Scrape Targets | Get information about all scrape targets |
