# deribit-bot

Windows machine is where you write and manage code — VS Code for editing, PowerShell and Git for pushing changes to GitHub.

GitHub is the central code store. Every change you make gets pushed here, and the Pi pulls from here to get the latest version.

Raspberry Pi is the heart of the system running everything 24/7. It has three main components — the trading bot, the API backend, and the dashboard frontend — all managed by systemd services that keep them alive through crashes and reboots.

Deribit testnet is where actual trades happen. The bot connects here via API keys stored in Keys.env to check prices and place orders.

Telegram receives instant notifications whenever a trade opens or closes.

Your browser connects to the Pi on port 5000 to view the live dashboard from any device on your network.