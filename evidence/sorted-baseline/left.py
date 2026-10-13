# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27.2,<0.28",
#     "rich>=13.7,<14",
#     "tomli>=2,<3",
# ]
# ///

from importlib.metadata import version
import json

import httpx
import tomli
from rich.console import Console
from rich.table import Table

configuration = tomli.loads('[report]\ntitle = "Sample report"\n')
transport = httpx.MockTransport(
    lambda request: httpx.Response(
        200, json={"rows": [{"name": "Example", "quantity": 17}]}
    )
)
with httpx.Client(transport=transport) as client:
    response = client.get("https://example.test/report")
    response.raise_for_status()
    rows = response.json()["rows"]

table = Table(title=configuration["report"]["title"])
table.add_column("Name")
table.add_column("Quantity")
for row in rows:
    table.add_row(row["name"], str(row["quantity"]))
Console(width=60, color_system=None).print(table)
print("VERSIONS=" + json.dumps(
    {name: version(name) for name in ("httpx", "rich", "tomli")}, sort_keys=True
))
