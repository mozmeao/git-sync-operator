from time import sleep
from tempfile import mkdtemp
import re

import sh
import yaml
from decouple import Csv, config
from munch import munchify


CONFIG_REPO = config('CONFIG_REPO')
CONFIG_DIR = config('CONFIG_DIR', default='/tmp/config')
GIT_SYNC_INTERVAL = config('GIT_SYNC_INTERVAL', default=60, cast=int)
MANAGED_NAMESPACES = config('MANAGED_NAMESPACES', cast=Csv())

# from https://stackoverflow.com/a/14693789
ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def kubemunch(*args):
    kubectl = sh.kubectl.bake('-o', 'yaml')
    munched = munchify(yaml.load(kubectl(args).stdout))
    if 'items' in munched.keys():
        # override items method
        munched.items = munched['items']
    return munched


def shallow_clone(repo=CONFIG_REPO, config_dir=CONFIG_DIR):
    sh.git('clone', '--depth', '1', repo, config_dir)
    sh.cd(config_dir)


def get_latest_commit():
    sh.git('pull')
    return sh.git('rev-parse', '--short', 'HEAD')


def get_current_commit():
    """
    Pull current commit from k8s CRD
    """
    # TODO


def git_updated_files(current_commit, latest_commit):
    """
    Return filenames that have been added or modified
    """
    if latest_commit != current_commit:
        diff = sh.git('diff', '--name-status', current_commit, latest_commit)
        for line in diff.splitlines():
            line = ansi_escape.sub('', line)
            # filter empty lines and deleted files
            if line.startswith(('M ', 'A ')):
                yield line.split()[1]
    else:
        return []


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
    sh.kubectl('apply', '-f', '-', _in=yaml.dump(
               {'apiVersion': 'versions.mozilla.org/v1',
                'kind': 'Version',
                'metadata': {'name': deployment.metadata.name},
                'deployed': version}))


def check_deployment(deployment, version):
    if deployment.metadata.annotations.get('applied-version',
                                           '') != version:
        sh.kubectl('annotate', '-n', deployment.metadata.namespace,
                   'deployment', deployment.metadata.name,
                   'applied-version={}'.format(version))
    elif (deployment.status.updatedReplicas ==
          deployment.status.replicas ==
          deployment.status.readyReplicas > 0):
        update_deployed_version(deployment, version)


def check_deployments(version):
    """
    Annotate deployments in managed namespaces with applied-commit
    This ensures that changes to Secrets and ConfigMaps are picked up
    Alert configured channel(s) when deployments are complete and store in CRD
    """
    for namespace in MANAGED_NAMESPACES:
        versions = kubemunch('get', '-n', namespace, 'versions').items
        finished_deployments = [v.metadata.name for v in versions
                                if v.get('deployed') == version]
        deployments = kubemunch('get', '-n', namespace, 'deployment').items
        for deployment in deployments:
            if deployment.metadata.name not in finished_deployments:
                check_deployment(deployment, version)


def apply_updates(current_commit, latest_commit):
    for updated_file in git_updated_files(current_commit, latest_commit):
        if '/' in updated_file and updated_file.endswith(
                ('.yaml', '.yml', '.json')):
            namespace, filename = updated_file.split('/', 1)
            if namespace in MANAGED_NAMESPACES:
                print('applying', filename, 'in', namespace)
                print(sh.kubectl('apply', '-n', namespace, '-f', filename))


def main():
    shallow_clone()
    while True:
        latest_commit = get_latest_commit()
        current_commit = get_current_commit()
        if latest_commit != current_commit:
            apply_updates(current_commit, latest_commit)
            # TODO: handle deletions or document lack of support for deletions
        check_deployments(latest_commit)
        sleep(GIT_SYNC_INTERVAL)
