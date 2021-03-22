.PHONY: test

SERVICE ?= discover-release
IMAGE_TAG ?= latest

clean:
	docker-compose down --volumes
	docker-compose rm -f

test:
	@echo "Testing..."
	docker-compose up --build --exit-code-from test test

docker:
	@echo "Building Docker container..."
	docker build --target service --tag pennsieve/$(SERVICE):$(IMAGE_TAG) .
	docker tag pennsieve/$(SERVICE):$(IMAGE_TAG) pennsieve/$(SERVICE):latest

publish: docker
	@echo "Pushing Docker container..."
	docker push pennsieve/$(SERVICE):$(IMAGE_TAG)
	docker push pennsieve/$(SERVICE):latest

format:
	docker-compose up --build --exit-code-from format format

lint:
	docker-compose up --build --exit-code-from lint lint
