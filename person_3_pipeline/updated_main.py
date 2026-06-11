import json
import os

# Import your graph builder class directly from your main file
from main import MovementGraphBuilder

def generate_local_graph():
    # 1. Dynamically get the exact folder this script is currently sitting in
    current_folder = os.path.dirname(os.path.abspath(__file__))

    # 2. Attach the filenames directly to that folder path
    input_file = os.path.join(current_folder, "assignments_optimal.json")
    output_file = os.path.join(current_folder, "my_local_graph_output.json")

    # 3. Make sure the input file actually exists
    if not os.path.exists(input_file):
        print(f"❌ ERROR: Could not find '{input_file}'")
        return

    # 2. Load Person 2's data
    print(f"📄 Loading data from {input_file}...")
    with open(input_file, "r") as f:
        person_2_data = json.load(f)

    print(f"⚙️ Building movement graph from {len(person_2_data)} observations...")

    # 3. Crunch the numbers using your class
    builder = MovementGraphBuilder()
    final_graph = builder.build_graph(person_2_data)

    # 4. Save the results locally without sending them to anyone
    with open(output_file, "w") as out_file:
        json.dump(final_graph, out_file, indent=2)

    print(f"✅ Success! Your isolated graph has been saved to '{output_file}'")


if __name__ == "__main__":
    generate_local_graph()