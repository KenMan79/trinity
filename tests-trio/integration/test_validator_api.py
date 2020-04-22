import platform

from async_service.trio import background_trio_service
import pytest
import trio

from eth2.clock import Clock
from eth2.validator_client.beacon_node import BeaconNode as BeaconNodeClient
from eth2.validator_client.client import Client as ValidatorClient
from eth2.validator_client.key_store import KeyStore
from trinity._utils.version import construct_trinity_client_identifier
from trinity.nodes.beacon.full import BeaconNode

# NOTE: seeing differences in ability to connect depending on platform.
# This could be specific to our trio HTTP server (somehow...) so try removing after deprecation...
local_host_name = "127.0.0.1"  # linux default
if platform.system() == "Darwin":
    local_host_name = "localhost"  # macOS variant


@pytest.mark.trio
async def test_beacon_node_and_validator_client_can_talk(
    autojump_clock,
    node_key,
    eth2_config,
    chain_config,
    database_dir,
    chain_class,
    get_trio_time,
    seconds_per_epoch,
    sample_bls_key_pairs,
    # NOTE: temporarily disable BLS while it standardizes
    no_op_bls,
):
    clock = Clock(
        eth2_config.SECONDS_PER_SLOT,
        chain_config.genesis_time,
        eth2_config.SLOTS_PER_EPOCH,
        seconds_per_epoch,
        time_provider=get_trio_time,
    )

    client_id = construct_trinity_client_identifier()
    validator_api_port = 0
    node = BeaconNode(
        node_key,
        eth2_config,
        chain_config,
        database_dir,
        chain_class,
        clock,
        validator_api_port,
        client_id,
    )

    starting_head_slot = node._chain.get_canonical_head().message.slot
    assert starting_head_slot == eth2_config.GENESIS_SLOT

    async with trio.open_nursery() as nursery:
        await nursery.start(node.run)

        api_client = BeaconNodeClient(
            chain_config.genesis_time,
            f"http://{local_host_name}:{node.validator_api_port}",
            eth2_config.SECONDS_PER_SLOT,
        )
        async with api_client:
            # sanity check
            assert api_client.client_version == client_id

            key_store = KeyStore(sample_bls_key_pairs)
            validator = ValidatorClient(key_store, clock, api_client)

            with trio.move_on_after(seconds_per_epoch):
                async with background_trio_service(validator):
                    await trio.sleep(seconds_per_epoch * 2)
            nursery.cancel_scope.cancel()
    sent_operations_for_broadcast = api_client._broadcast_operations
    received_operations_for_broadcast = node._api_context._broadcast_operations

    # temporary until we update to the new API
    # NOTE: with new API, remove assertion and deletion
    assert validator._duty_store._store[((1 << 64) - 1, 0)]
    del validator._duty_store._store[((1 << 64) - 1, 0)]

    # NOTE: this is the easiest condition to pass while suggesting this is working
    # As the other parts of the project shore up, we should do stricter testing to ensure
    # the operations we expect (and that they exist...) get across the gap from
    # validator to beacon node
    assert received_operations_for_broadcast.issubset(sent_operations_for_broadcast)
    assert node._chain.get_canonical_head().slot > starting_head_slot