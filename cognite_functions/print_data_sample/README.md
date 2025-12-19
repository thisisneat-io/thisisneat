# Print Data Sample Function

## Purpose

This is a simple diagnostic Cognite Function that prints the `data` parameter to stdout. Use this to capture sample input data for developing instance processing functions.

## How to Use

1. **Deploy the function** (see notebook below)
2. **Call the function with your data** (e.g., instances from DMS)
3. **Check the function logs** to see the printed output
4. **Copy the sample data** to use for developing your actual processing function

## Files

- `handler.py` - Simple function that prints data to stdout
- `requirements.txt` - Minimal dependencies
- `test_and_deploy.ipynb` - Notebook for deployment and testing
- `README.md` - This file

## Example Usage

### Deploy the Function

```python
from cognite.client import CogniteClient

client = CogniteClient()

function = client.functions.create(
    name="print-data-sample",
    folder="cognite_functions/print_data_sample",
    function_path="handler.py",
    description="Print data parameter to capture sample input"
)

print(f"Function created: {function.external_id}")
```

### Call with Sample Data

```python
# Example: Call with DMS instances
call = client.functions.call(
    external_id="print-data-sample",
    data={
        "instances": {
            "items": [
                {
                    "externalId": "asset-001",
                    "space": "my_space",
                    "instanceType": "node",
                    "type": {
                        "space": "model_space",
                        "externalId": "Asset",
                        "version": "v1"
                    },
                    "properties": {
                        "model_space": {
                            "AssetView/v1": {
                                "name": "Test Asset",
                                "type": "PUMP"
                            }
                        }
                    }
                }
            ]
        }
    }
)

# Wait for completion
call.wait()

# Check response
print(call.response)
```

### View the Logs

```python
# Get function logs
logs = client.functions.logs.list(
    external_id="print-data-sample",
    limit=10
)

for log in logs:
    print(log.message)
```

Or view logs in the Cognite Fusion UI:
1. Go to Functions
2. Find "print-data-sample"
3. Click on the latest call
4. View "Logs" tab

## What Gets Printed

The function prints:
- Data type
- Full data content (pretty-printed JSON)
- If instances detected:
  - Number of instances
  - Structure of the first instance
  - Summary of all instances

## Next Steps

Once you have captured the sample data structure:
1. Copy the sample from the logs
2. Use it to develop your actual instance processing function
3. Test with the known structure before deploying to production

