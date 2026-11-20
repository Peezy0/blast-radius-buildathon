PYTHON ?= python3
IMAGE ?= blast-radius-entry
SAMPLE := $(CURDIR)/official/sample
OUTPUT := $(CURDIR)/output.jsonl
RUN_FLAGS := --rm --platform linux/amd64 --network none --cpus 2 \
	--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
	--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
	--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
	--volume $(SAMPLE)/commits:/data/commits:ro \
	--volume $(SAMPLE)/tests.json:/data/tests.json:ro

.PHONY: test sample-local build sample

test:
	$(PYTHON) -m unittest discover -s tests -v

sample-local:
	$(PYTHON) selector.py --data-dir official/sample > $(OUTPUT)
	$(PYTHON) official/score.py official/sample official/sample/answers.json $(OUTPUT)

build:
	docker build --platform linux/amd64 --tag $(IMAGE) .

sample: build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	$(PYTHON) official/score.py official/sample official/sample/answers.json $(OUTPUT)
