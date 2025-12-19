"""
Simple Cognite Function to print data parameter to stdout.

Use this to capture sample input data for developing instance processing functions.
"""

import json


def handle(data, client):
    """
    Print the data parameter to stdout to capture sample input.
    
    Args:
        data: Input data from function call
        client: Cognite client instance
    
    Returns:
        dict: Summary of what was printed
    """
    print("=" * 80)
    print("DATA PARAMETER CONTENT")
    print("=" * 80)
    
    # Print data type
    print(f"\nData type: {type(data)}")
    
    # Print pretty JSON if possible
    print("\nData content:")
    print(json.dumps(data, indent=2, default=str))
    
    print("\n" + "=" * 80)
    
    # If data contains instances, print some details
    if isinstance(data, dict):
        if "instances" in data:
            print("\nINSTANCES DETECTED")
            print("=" * 80)
            instances = data["instances"]
            
            # Handle different instance formats
            if isinstance(instances, dict):
                if "items" in instances:
                    items = instances["items"]
                    
                    # Handle items as dict with keys (Cognite Functions format)
                    # Keys could be "n" (nodes), "e" (edges), etc.
                    if isinstance(items, dict):
                        total_count = 0
                        all_instances = []
                        
                        # Collect all instances from all keys
                        for key, value in items.items():
                            if isinstance(value, list):
                                count = len(value)
                                total_count += count
                                all_instances.extend(value)
                                print(f"  Key '{key}': {count} instance(s)")
                        
                        print(f"\nTotal instances across all keys: {total_count}")
                        
                        if all_instances:
                            print("\nFirst instance structure:")
                            print(json.dumps(all_instances[0], indent=2, default=str))
                            
                            if len(all_instances) > 1:
                                print(f"\n(+ {len(all_instances) - 1} more instances)")
                    # Handle items as direct list
                    elif isinstance(items, list):
                        print(f"Number of instances: {len(items)}")
                        
                        if items:
                            print("\nFirst instance structure:")
                            print(json.dumps(items[0], indent=2, default=str))
                            
                            if len(items) > 1:
                                print(f"\n(+ {len(items) - 1} more instances)")
            elif isinstance(instances, list):
                print(f"Number of instances: {len(instances)}")
                if instances:
                    print("\nFirst instance structure:")
                    print(json.dumps(instances[0], indent=2, default=str))
    
    # Return summary
    result = {
        "success": True,
        "data_type": str(type(data)),
        "data_keys": list(data.keys()) if isinstance(data, dict) else None,
        "message": "Data printed to stdout. Check function logs."
    }
    
    if isinstance(data, dict) and "instances" in data:
        instances_data = data["instances"]
        if isinstance(instances_data, dict) and "items" in instances_data:
            items = instances_data["items"]
            # Handle items as dict with any keys (n, e, etc.)
            if isinstance(items, dict):
                total_count = 0
                instance_types = {}
                for key, value in items.items():
                    if isinstance(value, list):
                        count = len(value)
                        total_count += count
                        instance_types[key] = count
                result["instance_count"] = total_count
                result["instance_types"] = instance_types
            # Handle items as direct list
            elif isinstance(items, list):
                result["instance_count"] = len(items)
        elif isinstance(instances_data, list):
            result["instance_count"] = len(instances_data)
    
    print("\nRETURN VALUE:")
    print(json.dumps(result, indent=2))
    print("=" * 80)
    
    return result

