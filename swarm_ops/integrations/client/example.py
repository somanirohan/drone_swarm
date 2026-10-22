"""30-line example: send a report, print what the swarm says. Usage: python example.py ws://127.0.0.1:8787/ops/ws TOKEN"""
import asyncio
import sys

from swarm_client import SwarmClient


async def main(url, token):
    client = SwarmClient(url, token)
    client.on_situation_report = lambda m: print("SITUATION", m["incident_id"], m["text"])
    client.on_alert = lambda m: print("ALERT", m["body"].get("alert"), m["text"])
    client.on_status = lambda m: print(m["kind"].upper(), m["from"], "->", m["to"], m["text"])
    client.start()
    await client.wait_ready()
    await client.send_control(orchestrator="external")      # we are the orchestrator now
    ack = await client.send_agency_request("demo-1", [100, 100], "medical", people=2, priority=4)
    print("ACK", ack["result"].get("reply_kind"), ack["result"].get("reason"))
    await asyncio.sleep(60)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:3]))
