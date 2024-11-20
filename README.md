# What is Trakt-to-Toggl?

This is a Python-based service designed to synchronize data from Trakt to Toggl.
The service supports periodic execution using Kubernetes CronJobs and can also be run manually in a Python environment for development or testing purposes.

## Project Structure

- **`app/`**: Contains the Python application.
  - `sync.py`: Main script for the synchronization logic.
  - `requirements.txt`: Lists Python dependencies.
  - `Dockerfile`: Defines how to build the container.

- **`k8s/`**: Kubernetes configuration files.
  - `base/`: Templates and base configuration for ConfigMaps and Secrets.
  - `secrets/`: Sensitive data (excluded from Git) for production use.

## Setup Instructions

### 1. Running Locally with Python

#### Prerequisites

- Python 3.8 or higher.
- A virtual environment tool (e.g., `venv` or `virtualenv`).

#### Steps

1. Create and activate a virtual environment

```
python3 -m venv trakt-to-toggl
source trakt-to-toggl/bin/activate
```

2. Install dependencies:

```
pip install -r app/requirements.txt
```

3. Set up environment variables: Create a .env file in the `app` directory and customize it with your keys and preferences:

```
TRAKT_CLIENT_ID=<your_client_id>
TRAKT_CLIENT_SECRET=<your_client_secret>
TRAKT_HISTORY_DAYS=7
TOGGL_API_TOKEN=...
TOGGL_WORKSPACE_ID=
TOGGL_PROJECT_ID=
TOGGL_TAGS=automated,trakt-to-toggl
```

4. Run the script:

```
python app/sync.py
```

Logs are provided on the standard output.

### 2. Running in Kubernetes

#### Steps

1. Create Kubernetes namespace:

```
kubectl apply -f k8s/base/namespace.yaml
```

2. Set up Secrets and ConfigMaps:

Use the k8s/base/secret-template.yaml and k8s/base/configmap-template.yaml to create your Secrets and ConfigMaps.
Apply them to the cluster:

```
kubectl apply -f k8s/secrets/trakt_tokens_secret.yaml
kubectl apply -f k8s/values/configmap-values.yaml
```

3. Deploy the CronJob:

```
kubectl apply -f k8s/base/cronjob.yaml
```

4. Test it with a manual run:

```
kubectl create job --from=cronjob/trakt-to-toggl trakt-to-toggl-manual-run
```

5. Check the logs:

```
kubectl logs trakt-to-toggl-manual-run-...
```
