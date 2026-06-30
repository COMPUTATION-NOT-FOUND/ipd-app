"""Student/local build — NO Firebase.

This stub stands in for the website's firebase_config so the local app runs with zero
Firebase dependency, no service-account key, and no credentials. All shared data
(submitted strategies, results) lives on the WEBSITE (the separate ipd-hub deployment)
and is reached over HTTP via hub_client — nothing here ever talks to Firestore.

`db` is None; any leftover website code paths that reference it are unreachable in the
local app (login/submission/results live only on the website).
"""

db = None
firebase_client_config = {}
