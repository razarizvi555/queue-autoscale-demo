CLUSTER := queue-demo
NS      := queue-demo

.PHONY: up cluster build load keda monitoring deploy deploy-warm burst watch stats grafana down lint pytest

up: cluster build load keda monitoring deploy   ## everything, in order

cluster:            ## create a 3-node kind cluster
	kind create cluster --config k8s/kind-config.yaml

build:              ## build the two images locally
	docker build -t queue-demo-api:dev api
	docker build -t queue-demo-worker:dev worker

load:               ## copy images into the kind nodes (no registry needed)
	kind load docker-image queue-demo-api:dev queue-demo-worker:dev --name $(CLUSTER)

keda:               ## install KEDA
	helm repo add kedacore https://kedacore.github.io/charts
	helm repo update
	helm upgrade --install keda kedacore/keda -n keda --create-namespace --wait

monitoring:         ## install Prometheus + Grafana (Grafana on localhost:3000)
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo update
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
	  -n monitoring --create-namespace --wait \
	  --set grafana.service.type=NodePort --set grafana.service.nodePort=30030 \
	  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false

deploy:             ## app + scale-to-zero autoscaling
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/redis.yaml -f k8s/api.yaml -f k8s/worker.yaml
	kubectl apply -f k8s/scaledobject.yaml
	kubectl apply -f k8s/podmonitor.yaml

deploy-warm:        ## switch to the warm-floor autoscaling policy
	kubectl apply -f k8s/scaledobject-warm.yaml

burst:              ## enqueue 200 jobs of 3s each
	./scripts/burst.sh 200 3

watch:              ## watch workers scale
	kubectl get pods -n $(NS) -l app=worker -w

stats:
	curl -s localhost:8080/stats

grafana:            ## print the Grafana admin password
	@kubectl get secret -n monitoring monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

down:               ## delete the cluster
	kind delete cluster --name $(CLUSTER)

lint:
	ruff check api worker tests

pytest:
	pytest -q
