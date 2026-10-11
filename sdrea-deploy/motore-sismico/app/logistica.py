"""
B.4.3 — Modulo Logistica (vedi la nota onesta su M4 in app/models.py,
sulla classe SquadraLogistica, per il rapporto con il modulo M4 della
spec Defense).
"""

from app.attenuation import haversine_km


def squadre_vicine(squadre: list, lat: float, lon: float, solo_disponibili: bool = True, limit: int = 5) -> list[dict]:
    """
    squadre: lista di oggetti SquadraLogistica (ORM). Ritorna una lista di
    dict ordinata per distanza crescente, con `distanza_km` calcolato.
    """
    risultati = []
    for s in squadre:
        if solo_disponibili and not s.disponibile:
            continue
        d = haversine_km(lat, lon, s.lat, s.lon)
        risultati.append({
            "id": s.id,
            "nome": s.nome,
            "tipo": s.tipo,
            "lat": s.lat,
            "lon": s.lon,
            "disponibile": s.disponibile,
            "distanza_km": round(d, 2),
            "note": s.note,
        })
    risultati.sort(key=lambda r: r["distanza_km"])
    return risultati[:limit]
