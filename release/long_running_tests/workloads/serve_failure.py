import os
import random
import string
import time
import asyncio

import requests

import ray
from ray import serve
from ray.serve.context import get_global_client
from ray.cluster_utils import Cluster
from ray._private.test_utils import safe_write_to_results_json

# Global variables / constants appear only right after imports.
# Ray serve deployment setup constants
NUM_REPLICAS = 7
MAX_BATCH_SIZE = 16

# Cluster setup constants
NUM_REDIS_SHARDS = 1
REDIS_MAX_MEMORY = 10**8
OBJECT_STORE_MEMORY = 10**8
NUM_NODES = 4

# RandomTest setup constants
CPUS_PER_NODE = 10
NUM_ITERATIONS = 350
ACTIONS_PER_ITERATION = 20

RAY_UNIT_TEST = "RAY_UNIT_TEST" in os.environ


def update_progress(result):
    """
    Write test result json to /tmp/, which will be read from
    anyscale product runs in each releaser test
    """
    result["last_update"] = time.time()
    safe_write_to_results_json(result)


cluster = Cluster()
for i in range(NUM_NODES):
    cluster.add_node(
        redis_port=6379 if i == 0 else None,
        num_redis_shards=NUM_REDIS_SHARDS if i == 0 else None,
        num_cpus=16,
        num_gpus=0,
        resources={str(i): 2},
        object_store_memory=OBJECT_STORE_MEMORY,
        redis_max_memory=REDIS_MAX_MEMORY,
        dashboard_host="0.0.0.0",
    )

ray.init(
    namespace="serve_failure_test",
    address=cluster.address,
    dashboard_host="0.0.0.0",
    log_to_driver=True,
)
serve.start(detached=True)


@ray.remote(max_restarts=-1, max_task_retries=-1)
class RandomKiller:
    def __init__(self, kill_period_s=1):
        self.kill_period_s = kill_period_s
        self.sanctuary = set()

    async def run(self):
        while True:
            chosen = random.choice(self._get_serve_actors())
            print(f"Killing {chosen}")
            ray.kill(chosen, no_restart=False)
            await asyncio.sleep(self.kill_period_s)

    async def spare(self, deployment_name: str):
        print(f'Sparing deployment "{deployment_name}" replicas.')
        self.sanctuary.add(deployment_name)

    async def stop_spare(self, deployment_name: str):
        print(f'No longer sparing deployment "{deployment_name}" replicas.')
        self.sanctuary.discard(deployment_name)

    def _get_serve_actors(self):
        controller = get_global_client()._controller
        routers = list(ray.get(controller.get_http_proxies.remote()).values())
        all_handles = routers + [controller]
        replica_dict = ray.get(controller._all_running_replicas.remote())
        for deployment_name, replica_info_list in replica_dict.items():
            if deployment_name not in self.sanctuary:
                for replica_info in replica_info_list:
                    all_handles.append(replica_info.actor_handle)

        return all_handles


class RandomTest:
    def __init__(self, random_killer_handle, max_deployments=1):
        self.max_deployments = max_deployments
        self.weighted_actions = [
            (self.create_deployment, 1),
            (self.verify_deployment, 4),
        ]
        self.deployments = []

        self.random_killer = random_killer_handle
        for _ in range(max_deployments):
            self.create_deployment()
        self.random_killer.run.remote()

    def create_deployment(self):
        if len(self.deployments) == self.max_deployments:
            deployment_to_delete = self.deployments.pop()
            serve.get_deployment(deployment_to_delete).delete()

        new_name = "".join([random.choice(string.ascii_letters) for _ in range(10)])

        @serve.deployment(name=new_name)
        def handler(self, *args):
            return new_name

        ray.get(self.random_killer.spare.remote(new_name))

        handler.deploy(_blocking=True)

        self.deployments.append(new_name)

        ray.get(self.random_killer.stop_spare.remote(new_name))

    def verify_deployment(self):
        deployment = random.choice(self.deployments)
        for _ in range(100):
            try:
                r = requests.get("http://127.0.0.1:8000/" + deployment)
                assert r.text == deployment
            except Exception:
                print("Request to {} failed.".format(deployment))
                time.sleep(0.01)

    def run(self):
        start_time = time.time()
        previous_time = start_time
        for iteration in range(NUM_ITERATIONS):
            for _ in range(ACTIONS_PER_ITERATION):
                actions, weights = zip(*self.weighted_actions)
                action_chosen = random.choices(actions, weights=weights)[0]
                print(f"Executing {action_chosen}")
                action_chosen()

            new_time = time.time()
            print(
                "Iteration {}:\n"
                "  - Iteration time: {}.\n"
                "  - Absolute time: {}.\n"
                "  - Total elapsed time: {}.".format(
                    iteration, new_time - previous_time, new_time, new_time - start_time
                )
            )
            update_progress(
                {
                    "iteration": iteration,
                    "iteration_time": new_time - previous_time,
                    "absolute_time": new_time,
                    "elapsed_time": new_time - start_time,
                }
            )
            previous_time = new_time

            if RAY_UNIT_TEST:
                break


random_killer = RandomKiller.remote()
tester = RandomTest(random_killer, max_deployments=NUM_NODES * CPUS_PER_NODE)
tester.run()
