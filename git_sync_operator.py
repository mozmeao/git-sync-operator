from time import sleep
import os

import sh
import yaml
from decouple import Csv, config
from munch import munchify


CONFIG_REPO = config('CONFIG_REPO')
CONFIG_DIR = config('CONFIG_DIR', default='/tmp/config')
CONFIG_BRANCH = config('CONFIG_BRANCH', default='master')
GIT_SYNC_INTERVAL = config('GIT_SYNC_INTERVAL', default=60, cast=int)
MANAGED_NAMESPACES = config('MANAGED_NAMESPACES', cast=Csv())


def kubemunch(*args):
    kubectl = sh.kubectl.bake('-o', 'yaml')
    munched = munchify(yaml.load(kubectl(args).stdout))
    if 'items' in munched.keys():
        # override items method
        munched.items = munched['items']
    return munched


def shallow_clone(repo=CONFIG_REPO, conf_dir=CONFIG_DIR, branch=CONFIG_BRANCH):
    sh.git('clone', '--depth', '1', repo, conf_dir, '-b', branch)
    sh.cd(conf_dir)


def get_latest_commit():
    sh.git('pull')
    return sh.git('rev-parse', '--short', 'HEAD')


def get_applied_version(namespace):
    for version in kubemunch('get', '-n', namespace, 'versions').items:
        if version.metadata.name == namespace:
            return version.applied


def update_applied_version(namespace, version):
    sh.kubectl('apply', '-n', namespace, '-f', '-', _in=yaml.dump(
               {'apiVersion': 'versions.mozilla.org/v1',
                'kind': 'Version',
                'metadata': {'name': namespace},
                'applied': version}))


def notify_new_relic(deployment, version):
    "TODO"


def notify_datadog(deployment, version):
    "TODO"


def notify_irc(deployment, version):
    "TODO"


def update_deployed_version(deployment, version):
    """
    notify configured channels
    """
    notify_new_relic(deployment, version)
    notify_datadog(deployment, version)
    notify_irc(deployment, version)
    vdict = {'apiVersion': 'versions.mozilla.org/v1',
             'kind': 'Version',
             'metadata': {'name': deployment.metadata.name},
             'deployed': version}
    if deployment.metadata.name == deployment.metadata.namespace:
        vdict['applied'] = version
    sh.kubectl('apply', '-n', deployment.metadata.namespace, '-f', '-',
               _in=yaml.dump(vdict))


def check_deployment(deployment, version):
    if deployment.metadata.annotations.get('applied-version',
                                           '') != version:
        # annotate first to ensure updated secrets and configmaps
        sh.kubectl('annotate', '-n', deployment.metadata.namespace,
                   'deployment', deployment.metadata.name,
                   'applied-version={}'.format(version))
    elif (deployment.status.updatedReplicas ==
          deployment.status.replicas ==
          deployment.status.readyReplicas > 0):
        update_deployed_version(deployment, version)


def check_deployments(version):
    for namespace in MANAGED_NAMESPACES:
        versions = kubemunch('get', '-n', namespace, 'versions').items
        finished_deployments = [v.metadata.name for v in versions
                                if v.get('deployed') == version]
        deployments = kubemunch('get', '-n', namespace, 'deployment').items
        for deployment in deployments:
            if deployment.metadata.name not in finished_deployments:
                check_deployment(deployment, version)


def apply_updates(namespace, version):
    if os.path.isdir(namespace):
        print(sh.kubectl('apply', '-n', namespace, '-f', namespace))
        update_applied_version(namespace, version)


def main():
    shallow_clone()
    while True:
        for namespace in MANAGED_NAMESPACES:
            version = get_latest_commit()
            if version != get_applied_version(namespace):
                apply_updates(namespace, version)
                # TODO: handle deletions or document lack of support for them
        check_deployments(version)
        sleep(GIT_SYNC_INTERVAL)
