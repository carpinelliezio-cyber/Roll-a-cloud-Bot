# Roll a Cloud — Bot Discord (setup complet du serveur)

## 1. Créer le bot
1. https://discord.com/developers/applications → **New Application** → onglet **Bot** → **Reset Token** (copie-le, ne le partage jamais).
2. Toujours dans **Bot** : active **Server Members Intent**.
3. **OAuth2 → URL Generator** : coche `bot` + `applications.commands`, permission **Administrator**. Ouvre le lien et ajoute le bot à ton serveur.

## 2. Configurer
Variables d'environnement :

| Variable | Obligatoire | Exemple |
|---|---|---|
| `DISCORD_TOKEN` | oui | le token du bot |
| `ROBLOX_PLACE_ID` | oui pour les stats | le nombre dans l'URL du jeu `roblox.com/games/123456789/...` |
| `ROBLOX_GROUP_ID` | non | ajoute le compteur de membres du groupe |
| `VIP_GAMEPASS_ID` | non | rôle 💎 VIP automatique si le joueur possède le gamepass |
| `TIKTOK_URL` / `YOUTUBE_URL` | non | apparaissent dans #links |

## 3. Lancer
```bash
pip install -r requirements.txt
export DISCORD_TOKEN="..."
export ROBLOX_PLACE_ID="123456789"
python bot.py
```
Windows (PowerShell) : `$env:DISCORD_TOKEN="..."` au lieu de `export`.

Le bot doit tourner en continu (PC allumé, VPS, Railway, etc.) pour les boutons, la vérification et les stats.

## 4. Construire le serveur
Dans Discord, tape **/setup**. Le bot crée tout ce qui manque. Il ne supprime rien : tu peux le relancer sans risque.
Ensuite, donne-toi les rôles **👑 Owner** et **☁️ Verified**.

## Commandes
- `/setup` (admin) : rôles, salons, permissions et contenu
- `/stats` : stats Roblox en direct
- `/whois @membre` : compte Roblox lié
- `/update` : met à jour le pseudo et le rôle VIP
- `/unlink @membre` (staff) : retire la liaison Roblox
- `/refreshstats` (admin) : force la mise à jour des compteurs

## Remarques
- Les compteurs de stats se mettent à jour toutes les 10 min (c'est la limite de Discord pour renommer un salon).
- `#announcements` et `#updates` deviennent des salons d'annonces (que les autres serveurs peuvent suivre) si la **Communauté** est activée sur le serveur.
- Les textes (FAQ, how-to-play, règles) se trouvent dans `post_content()` de `bot.py` : adapte-les aux vraies mécaniques du jeu.
- Les données sont dans `server_state.json` et `linked_accounts.json` : garde ces fichiers.
