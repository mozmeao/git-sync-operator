#!/bin/bash
docker build . -t mozmeao/git-sync-operator:${GIT_COMMIT:=$(git rev-parse --short HEAD)}
docker push mozmeao/git-sync-operator:${GIT_COMMIT}
