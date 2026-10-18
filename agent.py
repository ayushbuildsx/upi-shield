from uagents import Agent
from protocols import upi_shield_protocol

agent = Agent()

agent.include(upi_shield_protocol, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
