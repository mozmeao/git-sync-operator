from datetime import datetime
from time import sleep
import os
import traceback

import boto3
import sh
import yaml
from decouple import Csv, config
from munch import munchify


CONFIG_REPO = config('CONFIG_REPO')
CONFIG_DIR = config('CONFIG_DIR', default='/tmp/config')
CONFIG_BRANCH = config('CONFIG_BRANCH', default='master')
GIT_SYNC_INTERVAL = config('GIT_SYNC_INTERVAL', default=60, cast=int)
MANAGED_NAMESPACES = config('MANAGED_NAMESPACES', cast=Csv())
S3_BUCKET = config('S3_BUCKET', default='')
# cluster name is not currently available from API: kubernetes/federation#132
CLUSTER_NAME = config('CLUSTER_NAME', default='')



def kubectl(*args, **kwargs):
    try:
        return sh.kubectl(*args, **kwargs)
    except sh.ErrorReturnCode as e:
        print(e)


def git(*args, **kwargs):
    try:
        return str(sh.contrib.git(*args, **kwargs)).strip()
    except sh.ErrorReturnCode as e:
        print(e)


def kubemunch(*args):
    if 'yaml' not in args:
        args += ('-o', 'yaml')
    result = kubectl(*args)
    if result:
        munched = munchify(yaml.load(result.stdout))
        if 'items' in munched.keys():
            # override items method
            munched.items = munched['items']
        return munched


def shallow_clone(repo=CONFIG_REPO, conf_dir=CONFIG_DIR, branch=CONFIG_BRANCH):
    git('clone', '--depth', '1', repo, conf_dir, '-b', branch)


def get_latest_commit():
    git('pull')
    return git('rev-parse', '--short', 'HEAD')


def get_applied_version(namespace):
    version = kubemunch('get', '-n', namespace, 'versions', namespace)
    if version:
        return version.applied


def update_applied_version(namespace, version):
    vdict = {'apiVersion': 'mozilla.org/v1',
             'kind': 'Version',
             'metadata': {'name': namespace},
             'applied': version}
    print('updating applied version:', vdict)
    kubectl('apply', '-n', namespace, '-f', '-', _in=yaml.dump(vdict))


def log_deployment_s3(deployment, version):
    if not S3_BUCKET or not CLUSTER_NAME:
        return
    key = '/'.join([CLUSTER_NAME, deployment.metadata.namespace,
                    deployment.metadata.name, version])
    body = datetime.utcnow().isoformat()
    client = boto3.client('s3')
    print('put s3://', S3_BUCKET, '/', key)
    client.put_object(Body=body, Bucket=S3_BUCKET, Key=key, ACL='public-read')


def update_deployed_version(deployment, version):
    vdict = {'apiVersion': 'mozilla.org/v1',
             'kind': 'Version',
             'metadata': {'name': deployment.metadata.name},
             'deployed': version}
    if deployment.metadata.name == deployment.metadata.namespace:
        vdict['applied'] = version
    print('updating deployed version:', vdict)
    kubectl('apply', '-n', deployment.metadata.namespace, '-f', '-',
            _in=yaml.dump(vdict))
    log_deployment_s3(deployment, version)


def check_deployment(deployment, version):
    if deployment.metadata.annotations.get('applied-version',
                                           '') != version:
        # annotate first to ensure updated secrets and configmaps
        kubectl('annotate', '-n', deployment.metadata.namespace,
                'deployment', deployment.metadata.name, '--overwrite',
                'applied-version={}'.format(version))
    elif (deployment.status.updatedReplicas ==
          deployment.status.replicas ==
          deployment.status.readyReplicas > 0):
        update_deployed_version(deployment, version)


def check_deployments(version):
    for namespace in MANAGED_NAMESPACES:
        versions = kubemunch('get', '-n', namespace, 'versions')
        if versions:
            finished_deployments = [v.metadata.name for v in versions.items
                                    if v.get('deployed') == version]
            deployments = kubemunch('get', '-n', namespace, 'deployment')
            if deployments:
                for deployment in deployments.items:
                    if deployment.metadata.name not in finished_deployments:
                        check_deployment(deployment, version)


def apply_updates(namespace, version):
    if os.path.isdir(namespace):
        result = kubectl('apply', '-n', namespace, '-f', namespace)
        if result:
            print(result)
            update_applied_version(namespace, version)


def main():
    shallow_clone()
    sh.cd(CONFIG_DIR)
    while True:
        for namespace in MANAGED_NAMESPACES:
            try:
                version = get_latest_commit()
                print('latest commit:', version)
                applied_version = get_applied_version(namespace)
                print('applied version:', applied_version)
                if version != applied_version:
                    apply_updates(namespace, version)
                    # TODO: handle deletions or document lack of support
                check_deployments(version)
            except Exception as e:
                traceback.print_exc()
        sleep(GIT_SYNC_INTERVAL)


if __name__ == '__main__':
    main()
