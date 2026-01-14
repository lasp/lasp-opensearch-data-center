"""Test ingesting a CSV file of packet data"""
# Installed
import uuid
import time
from opensearchpy import helpers
from lasp_opensearch_data_center.lambda_functions.opensearch_data_center_lambda_runtime import index_sunset_handler

# --- Helper for generating dummy data ---
def _generate_docs(index_name, count=100):
    """Generates a list of dummy documents for bulk insertion."""
    for _ in range(count):
        yield {
            "_index": index_name,
            "_source": {
                "timestamp": time.time(),
                "data": str(uuid.uuid4()) * 10, 
                "status": "active"
            }
        }

def test_index_rotation_triggers_on_large_size(opensearch_container, _opensearch_env):
    """
    Verifies that if an index exceeds the size threshold, it is renamed/aliased.
    """
    client = opensearch_container.get_client()

    index_name = "telemetry-data"
    
    # 1. Create the initial index
    client.indices.create(index=index_name)
    
    # 2. Populate it with enough data to exceed a small threshold
    #    We write ~500 docs. With the padding above, this should easily exceed 10KB.
    helpers.bulk(client, _generate_docs(index_name, count=500))
    
    # 3. CRITICAL: Force refresh and flush.
    #    Without this, OpenSearch stats API might report store.size_in_bytes = 0
    client.indices.refresh(index=index_name)
    client.indices.flush(index=index_name)
    
    # Verify we actually have size (sanity check for the test itself)
    stats = client.indices.stats(index=index_name)
    size_in_bytes = stats["indices"][index_name]["primaries"]["store"]["size_in_bytes"]
    print(f"Current Index Size: {size_in_bytes} bytes")
    assert size_in_bytes > 0, "Test setup failed: Index has no size!"

    # Now we mimic the step function logic in code below 

    # Find "large" indicies (in this case we set the threshold absurdly small for unit testing)
    large_indexes = index_sunset_handler.handler({
                                                  'step':'find_large_indexes', 
                                                  'execution_input' : {'threshold_override': .00001}
                                                 }, 
                                                 None)
                                                 
    # Archive the large indexes
    status = index_sunset_handler.handler({
                                           'step':'kickoff_archival', 
                                           'index' : large_indexes[0] # The step function will provide one large index at a time
                                          }, 
                                          None)
    
    # Check that archive is complete. 
    # We mimic the behavior of the "wait" loop here
    for _ in range(0,5):
        time.sleep(1)
        status = index_sunset_handler.handler(status, None)
        if status['status'] == 'COMPLETED':
            break

    assert status['status'] == 'COMPLETED'

    # Perform cleanup 
    status = index_sunset_handler.handler(status, None)
    
    # Wait a few seconds for opensearch to finish cleanup
    time.sleep(3)

    # ASSERTIONS

    #Check the original name + "combined" is now an alias
    index_alias = index_name+"-combined"
    is_alias = client.indices.exists_alias(name=index_alias)
    assert is_alias is True, f"{index_name} should have been converted to an alias"
    
    # B. Check the data still exists via the alias
    #    It should still return the 500 docs we created
    client.indices.refresh() # Refresh again to see the re-indexed data
    count = client.count(index=index_alias)["count"]
    assert count == 500, "Data was lost during rotation!"

    # C. Check the underlying backing index has changed name
    aliases = client.indices.get_alias(name=index_alias)
    backing_indices = list(aliases.keys())
    assert index_name not in backing_indices, "The backing index should have a new name"
    assert "telemetry-data-" in backing_indices[0], "Backing index should be renamed"