PS99 Discord Bot v6.1.4 patch

1. Pune acest fisier in folderul proiectului, langa bot.py.
2. Deschide PowerShell in acel folder.
3. Ruleaza:
   powershell -ExecutionPolicy Bypass -File .\bot_v6.1.4_patch.ps1
4. Testeaza:
   python -m py_compile bot.py
   python -m pytest -q
5. Porneste:
   python bot.py

Patch-ul:
- actualizeaza versiunea la 6.1.4;
- rezolva Roblox UserID -> DisplayName prin Roblox Users API;
- aplica numele pentru Owner, Player Names si Top Contributions;
- pastreaza fallback la UserID daca Roblox API nu raspunde;
- adauga un workflow GitHub Actions pentru pornirea botului.

ATENTIE:
GitHub Actions nu este o solutie 24/7; runnerul are o limita de timp. Pentru hosting permanent foloseste un serviciu de deployment.
Nu comite DISCORD_TOKEN in GitHub. Adauga-l in Settings -> Secrets and variables -> Actions ca secret numit DISCORD_TOKEN.
