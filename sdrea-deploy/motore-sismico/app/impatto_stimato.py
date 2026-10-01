"""
Risposta rapida post-evento — il vero valore aggiunto realistico del
motore sismico, distinto e più onesto della "previsione sismica" (che non
esiste scientificamente: nessun sistema al mondo prevede quando/dove
avverrà un terremoto, e SDREA non lo promette).

Due funzioni, entrambe standard in sismologia dell'ingegneria, non
inventate per questo progetto:

1. `stima_impatto_su_comuni` — usa il modello di attenuazione già presente
   in app/attenuation.py (Sabetta-Pugliese, coefficienti DA VALIDARE come
   già dichiarato in quel file) per stimare, appena arriva un evento reale
   da INGV, l'intensità di scuotimento attesa (PGA) in un elenco di comuni
   vicini — stessa logica di principio di ShakeMap (USGS/INGV), utile per
   dare priorità immediata alle ispezioni nei primi minuti dopo un evento,
   quando non c'è ancora nessun sopralluogo.

2. `stima_probabilita_repliche` — legge di Omori-Utsu, formula empirica
   consolidata (dal 1894, raffinata da Utsu nel 1961) per il decadimento
   nel tempo del tasso di scosse di assestamento dopo un evento principale.
   Anche qui i parametri (K, c, p) sono tipici della letteratura sismologica
   italiana ma NON calibrati sulla sismicità specifica di questa zona — DA
   VALIDARE con INGV/Dipartimento prima di un uso operativo che vada oltre
   l'indicazione di massima.
"""

from __future__ import annotations

import math
import os

from app.attenuation import haversine_km, stima_intensita_pga

# RAGGIO_MASSIMO_KM: se l'epicentro è più lontano di così da OGNI comune
# del catalogo sotto, la lista "comuni più esposti" resta vuota invece di
# mostrare comunque i comuni più vicini con una PGA trascurabile — un
# evento a centinaia di km di distanza non ha comuni "esposti" pertinenti
# nel catalogo, punto (bug segnalato dall'utente e corretto qui). Con la
# copertura ora estesa a tutta l'Emilia-Romagna (vedi sotto), 150km resta
# un raggio ragionevole: qualunque punto della regione ha quasi sempre un
# comune del catalogo nel raggio di poche decine di km.
RAGGIO_MASSIMO_KM = 150.0
# Elenco di tutti i 330 comuni dell'Emilia-Romagna (coordinate reali,
# centroide della sede municipale, standard EPSG:4326) — PRIMA copriva solo
# 9 comuni dell'area ferrarese: in vista della candidatura di SDREA a
# Great-ER 2.0 (progetto regionale), serve una copertura dell'intera
# regione, non solo di una provincia.
#
# FONTE: dataset pubblico "comuni-italiani" (opendatasicilia su GitHub),
# file dati/main.csv, verificato il 01/10/2026 — 330 comuni risultanti per
# la regione Emilia-Romagna (coerente con il numero ufficiale ISTAT dopo le
# fusioni comunali). Un singolo valore era palesemente corrotto nella fonte
# (Montegridolfo: latitudine "43859" invece di "43.859", punto decimale
# mancante) — corretto qui dopo verifica di plausibilità geografica
# (provincia di Rimini, longitudine coerente), ma NON confermato da una
# seconda fonte indipendente: da ricontrollare se questo singolo comune
# diventa rilevante in un caso reale.
#
# Resta un limite dichiarato: questo è un catalogo dei CAPOLUOGHI comunali
# (un punto per comune), non delle infrastrutture specifiche (ponti, dighe,
# scuole, ospedali) — per quello esiste già il progetto collegato
# "Ponti e Daghi" (strutture puntuali valutate separatamente). Qui serve
# solo a dare priorità di ispezione per area quando arriva un evento reale.
COMUNI_EMILIA_ROMAGNA = [
    {"nome": 'Alto Reno Terme', "lat": 44.123954, "lon": 10.937306},  # Bologna
    {"nome": "Anzola dell'Emilia", "lat": 44.546684, "lon": 11.193326},  # Bologna
    {"nome": 'Argelato', "lat": 44.64157, "lon": 11.34781},  # Bologna
    {"nome": 'Baricella', "lat": 44.646165, "lon": 11.533295},  # Bologna
    {"nome": 'Bentivoglio', "lat": 44.635792, "lon": 11.417193},  # Bologna
    {"nome": 'Bologna', "lat": 44.493758, "lon": 11.343031},  # Bologna
    {"nome": 'Borgo Tossignano', "lat": 44.27569, "lon": 11.589844},  # Bologna
    {"nome": 'Budrio', "lat": 44.537863, "lon": 11.535498},  # Bologna
    {"nome": 'Calderara di Reno', "lat": 44.563052, "lon": 11.271693},  # Bologna
    {"nome": 'Camugnano', "lat": 44.170457, "lon": 11.088812},  # Bologna
    {"nome": 'Casalecchio di Reno', "lat": 44.476018, "lon": 11.275686},  # Bologna
    {"nome": 'Casalfiumanese', "lat": 44.296864, "lon": 11.624022},  # Bologna
    {"nome": 'Castel Guelfo di Bologna', "lat": 44.43114, "lon": 11.67741},  # Bologna
    {"nome": 'Castel Maggiore', "lat": 44.575685, "lon": 11.36481},  # Bologna
    {"nome": 'Castel San Pietro Terme', "lat": 44.40127, "lon": 11.585499},  # Bologna
    {"nome": "Castel d'Aiano", "lat": 44.275859, "lon": 10.999408},  # Bologna
    {"nome": 'Castel del Rio', "lat": 44.214292, "lon": 11.504082},  # Bologna
    {"nome": 'Castel di Casio', "lat": 44.162956, "lon": 11.035811},  # Bologna
    {"nome": "Castello d'Argile", "lat": 44.681321, "lon": 11.296169},  # Bologna
    {"nome": 'Castenaso', "lat": 44.50887, "lon": 11.469811},  # Bologna
    {"nome": 'Castiglione dei Pepoli', "lat": 44.142505, "lon": 11.166508},  # Bologna
    {"nome": 'Crevalcore', "lat": 44.72057, "lon": 11.149804},  # Bologna
    {"nome": 'Dozza', "lat": 44.359391, "lon": 11.628879},  # Bologna
    {"nome": 'Fontanelice', "lat": 44.260098, "lon": 11.559736},  # Bologna
    {"nome": 'Gaggio Montano', "lat": 44.196956, "lon": 10.932908},  # Bologna
    {"nome": 'Galliera', "lat": 44.749163, "lon": 11.426533},  # Bologna
    {"nome": "Granarolo dell'Emilia", "lat": 44.55321, "lon": 11.443208},  # Bologna
    {"nome": 'Grizzana Morandi', "lat": 44.25736, "lon": 11.15289},  # Bologna
    {"nome": 'Imola', "lat": 44.353514, "lon": 11.714123},  # Bologna
    {"nome": 'Lizzano in Belvedere', "lat": 44.161829, "lon": 10.894044},  # Bologna
    {"nome": 'Loiano', "lat": 44.266157, "lon": 11.325534},  # Bologna
    {"nome": 'Malalbergo', "lat": 44.718674, "lon": 11.532212},  # Bologna
    {"nome": 'Marzabotto', "lat": 44.340988, "lon": 11.204782},  # Bologna
    {"nome": 'Medicina', "lat": 44.476674, "lon": 11.638338},  # Bologna
    {"nome": 'Minerbio', "lat": 44.624199, "lon": 11.490697},  # Bologna
    {"nome": 'Molinella', "lat": 44.620853, "lon": 11.670237},  # Bologna
    {"nome": 'Monghidoro', "lat": 44.219859, "lon": 11.318747},  # Bologna
    {"nome": 'Monte San Pietro', "lat": 44.428404, "lon": 11.15122},  # Bologna
    {"nome": 'Monterenzio', "lat": 44.326882, "lon": 11.406144},  # Bologna
    {"nome": 'Monzuno', "lat": 44.279263, "lon": 11.266408},  # Bologna
    {"nome": 'Mordano', "lat": 44.397426, "lon": 11.810857},  # Bologna
    {"nome": "Ozzano dell'Emilia", "lat": 44.44498, "lon": 11.47605},  # Bologna
    {"nome": 'Pianoro', "lat": 44.392964, "lon": 11.342314},  # Bologna
    {"nome": 'Pieve di Cento', "lat": 44.713209, "lon": 11.306598},  # Bologna
    {"nome": 'Sala Bolognese', "lat": 44.629603, "lon": 11.272748},  # Bologna
    {"nome": 'San Benedetto Val di Sambro', "lat": 44.215259, "lon": 11.232914},  # Bologna
    {"nome": 'San Giorgio di Piano', "lat": 44.646793, "lon": 11.375449},  # Bologna
    {"nome": 'San Giovanni in Persiceto', "lat": 44.638978, "lon": 11.187131},  # Bologna
    {"nome": 'San Lazzaro di Savena', "lat": 44.471567, "lon": 11.404859},  # Bologna
    {"nome": 'San Pietro in Casale', "lat": 44.699052, "lon": 11.402926},  # Bologna
    {"nome": "Sant'Agata Bolognese", "lat": 44.664169, "lon": 11.134004},  # Bologna
    {"nome": 'Sasso Marconi', "lat": 44.399632, "lon": 11.247554},  # Bologna
    {"nome": 'Valsamoggia', "lat": 44.439824, "lon": 11.059275},  # Bologna
    {"nome": 'Vergato', "lat": 44.28266, "lon": 11.10731},  # Bologna
    {"nome": 'Zola Predosa', "lat": 44.489665, "lon": 11.218309},  # Bologna
    {"nome": 'Argenta', "lat": 44.614338, "lon": 11.833842},  # Ferrara
    {"nome": 'Bondeno', "lat": 44.886177, "lon": 11.410807},  # Ferrara
    {"nome": 'Cento', "lat": 44.72744, "lon": 11.290303},  # Ferrara
    {"nome": 'Codigoro', "lat": 44.831581, "lon": 12.109524},  # Ferrara
    {"nome": 'Comacchio', "lat": 44.696379, "lon": 12.180427},  # Ferrara
    {"nome": 'Copparo', "lat": 44.894183, "lon": 11.827889},  # Ferrara
    {"nome": 'Ferrara', "lat": 44.766764, "lon": 11.827939},  # Ferrara
    {"nome": 'Fiscaglia', "lat": 44.794133, "lon": 12.029515},  # Ferrara
    {"nome": 'Goro', "lat": 44.850883, "lon": 12.294927},  # Ferrara
    {"nome": 'Jolanda di Savoia', "lat": 44.882408, "lon": 11.979349},  # Ferrara
    {"nome": 'Lagosanto', "lat": 44.76198, "lon": 12.142825},  # Ferrara
    {"nome": 'Masi Torello', "lat": 44.794578, "lon": 11.801817},  # Ferrara
    {"nome": 'Mesola', "lat": 44.920782, "lon": 12.229224},  # Ferrara
    {"nome": 'Ostellato', "lat": 44.745678, "lon": 11.943521},  # Ferrara
    {"nome": 'Poggio Renatico', "lat": 44.765974, "lon": 11.490111},  # Ferrara
    {"nome": 'Portomaggiore', "lat": 44.699258, "lon": 11.804078},  # Ferrara
    {"nome": 'Riva del Po', "lat": 44.96047, "lon": 11.894394},  # Ferrara
    {"nome": 'Terre del Reno', "lat": 44.812155, "lon": 11.404388},  # Ferrara
    {"nome": 'Tresignana', "lat": 44.828937, "lon": 11.878917},  # Ferrara
    {"nome": 'Vigarano Mainarda', "lat": 44.843422, "lon": 11.494436},  # Ferrara
    {"nome": 'Voghiera', "lat": 44.759074, "lon": 11.751132},  # Ferrara
    {"nome": 'Bagno di Romagna', "lat": 43.835227, "lon": 11.959598},  # Forlì-Cesena
    {"nome": 'Bertinoro', "lat": 44.148665, "lon": 12.134235},  # Forlì-Cesena
    {"nome": 'Borghi', "lat": 44.031591, "lon": 12.354389},  # Forlì-Cesena
    {"nome": 'Castrocaro Terme e Terra del Sole', "lat": 44.182365, "lon": 11.955631},  # Forlì-Cesena
    {"nome": 'Cesena', "lat": 44.136352, "lon": 12.242244},  # Forlì-Cesena
    {"nome": 'Cesenatico', "lat": 44.199928, "lon": 12.396969},  # Forlì-Cesena
    {"nome": 'Civitella di Romagna', "lat": 44.007171, "lon": 11.941346},  # Forlì-Cesena
    {"nome": 'Dovadola', "lat": 44.122574, "lon": 11.888255},  # Forlì-Cesena
    {"nome": 'Forlimpopoli', "lat": 44.187305, "lon": 12.125906},  # Forlì-Cesena
    {"nome": 'Forlì', "lat": 44.222728, "lon": 12.041273},  # Forlì-Cesena
    {"nome": 'Galeata', "lat": 43.996663, "lon": 11.912006},  # Forlì-Cesena
    {"nome": 'Gambettola', "lat": 44.116946, "lon": 12.338764},  # Forlì-Cesena
    {"nome": 'Gatteo', "lat": 44.109466, "lon": 12.386042},  # Forlì-Cesena
    {"nome": 'Longiano', "lat": 44.074378, "lon": 12.327473},  # Forlì-Cesena
    {"nome": 'Meldola', "lat": 44.126721, "lon": 12.061153},  # Forlì-Cesena
    {"nome": 'Mercato Saraceno', "lat": 43.956744, "lon": 12.195766},  # Forlì-Cesena
    {"nome": 'Modigliana', "lat": 44.161163, "lon": 11.793528},  # Forlì-Cesena
    {"nome": 'Montiano', "lat": 44.082965, "lon": 12.30444},  # Forlì-Cesena
    {"nome": 'Portico e San Benedetto', "lat": 44.003714, "lon": 11.720184},  # Forlì-Cesena
    {"nome": 'Predappio', "lat": 44.102215, "lon": 11.98162},  # Forlì-Cesena
    {"nome": 'Premilcuore', "lat": 43.975908, "lon": 11.777527},  # Forlì-Cesena
    {"nome": 'Rocca San Casciano', "lat": 44.059096, "lon": 11.842061},  # Forlì-Cesena
    {"nome": 'Roncofreddo', "lat": 44.04224, "lon": 12.31826},  # Forlì-Cesena
    {"nome": 'San Mauro Pascoli', "lat": 44.106463, "lon": 12.414898},  # Forlì-Cesena
    {"nome": 'Santa Sofia', "lat": 43.947267, "lon": 11.908451},  # Forlì-Cesena
    {"nome": 'Sarsina', "lat": 43.918758, "lon": 12.143258},  # Forlì-Cesena
    {"nome": 'Savignano sul Rubicone', "lat": 44.092512, "lon": 12.399131},  # Forlì-Cesena
    {"nome": 'Sogliano al Rubicone', "lat": 44.004663, "lon": 12.300541},  # Forlì-Cesena
    {"nome": 'Tredozio', "lat": 44.081042, "lon": 11.744208},  # Forlì-Cesena
    {"nome": 'Verghereto', "lat": 43.792956, "lon": 12.003938},  # Forlì-Cesena
    {"nome": 'Bastiglia', "lat": 44.726969, "lon": 10.9986},  # Modena
    {"nome": 'Bomporto', "lat": 44.72977, "lon": 11.038401},  # Modena
    {"nome": 'Campogalliano', "lat": 44.690512, "lon": 10.840734},  # Modena
    {"nome": 'Camposanto', "lat": 44.790101, "lon": 11.137808},  # Modena
    {"nome": 'Carpi', "lat": 44.78357, "lon": 10.885452},  # Modena
    {"nome": 'Castelfranco Emilia', "lat": 44.595024, "lon": 11.052901},  # Modena
    {"nome": 'Castelnuovo Rangone', "lat": 44.551334, "lon": 10.935016},  # Modena
    {"nome": 'Castelvetro di Modena', "lat": 44.503462, "lon": 10.9433},  # Modena
    {"nome": 'Cavezzo', "lat": 44.8365, "lon": 11.029435},  # Modena
    {"nome": 'Concordia sulla Secchia', "lat": 44.913174, "lon": 10.983297},  # Modena
    {"nome": 'Fanano', "lat": 44.207737, "lon": 10.793837},  # Modena
    {"nome": 'Finale Emilia', "lat": 44.834574, "lon": 11.293905},  # Modena
    {"nome": 'Fiorano Modenese', "lat": 44.539143, "lon": 10.811848},  # Modena
    {"nome": 'Fiumalbo', "lat": 44.179951, "lon": 10.647733},  # Modena
    {"nome": 'Formigine', "lat": 44.575143, "lon": 10.847028},  # Modena
    {"nome": 'Frassinoro', "lat": 44.295555, "lon": 10.570198},  # Modena
    {"nome": 'Guiglia', "lat": 44.424132, "lon": 10.962119},  # Modena
    {"nome": 'Lama Mocogno', "lat": 44.307263, "lon": 10.731777},  # Modena
    {"nome": 'Maranello', "lat": 44.525522, "lon": 10.866361},  # Modena
    {"nome": 'Marano sul Panaro', "lat": 44.455898, "lon": 10.966384},  # Modena
    {"nome": 'Medolla', "lat": 44.846673, "lon": 11.0676},  # Modena
    {"nome": 'Mirandola', "lat": 44.887207, "lon": 11.066106},  # Modena
    {"nome": 'Modena', "lat": 44.538473, "lon": 10.935961},  # Modena
    {"nome": 'Montecreto', "lat": 44.245955, "lon": 10.717602},  # Modena
    {"nome": 'Montefiorino', "lat": 44.352857, "lon": 10.625599},  # Modena
    {"nome": 'Montese', "lat": 44.268506, "lon": 10.941189},  # Modena
    {"nome": 'Nonantola', "lat": 44.677608, "lon": 11.043624},  # Modena
    {"nome": 'Novi di Modena', "lat": 44.893439, "lon": 10.901254},  # Modena
    {"nome": 'Palagano', "lat": 44.320957, "lon": 10.6461},  # Modena
    {"nome": 'Pavullo nel Frignano', "lat": 44.339694, "lon": 10.834113},  # Modena
    {"nome": 'Pievepelago', "lat": 44.204754, "lon": 10.616601},  # Modena
    {"nome": 'Polinago', "lat": 44.344758, "lon": 10.723501},  # Modena
    {"nome": 'Prignano sulla Secchia', "lat": 44.43836, "lon": 10.691899},  # Modena
    {"nome": 'Ravarino', "lat": 44.72117, "lon": 11.098403},  # Modena
    {"nome": 'Riolunato', "lat": 44.229974, "lon": 10.651565},  # Modena
    {"nome": 'San Cesario sul Panaro', "lat": 44.561136, "lon": 11.033218},  # Modena
    {"nome": 'San Felice sul Panaro', "lat": 44.839415, "lon": 11.143165},  # Modena
    {"nome": 'San Possidonio', "lat": 44.892089, "lon": 10.996136},  # Modena
    {"nome": 'San Prospero', "lat": 44.789671, "lon": 11.0221},  # Modena
    {"nome": 'Sassuolo', "lat": 44.540977, "lon": 10.782902},  # Modena
    {"nome": 'Savignano sul Panaro', "lat": 44.480538, "lon": 11.035109},  # Modena
    {"nome": 'Serramazzoni', "lat": 44.422462, "lon": 10.789147},  # Modena
    {"nome": 'Sestola', "lat": 44.230455, "lon": 10.771404},  # Modena
    {"nome": 'Soliera', "lat": 44.738103, "lon": 10.924393},  # Modena
    {"nome": 'Spilamberto', "lat": 44.533413, "lon": 11.024083},  # Modena
    {"nome": 'Vignola', "lat": 44.478414, "lon": 11.009093},  # Modena
    {"nome": 'Zocca', "lat": 44.347264, "lon": 10.990407},  # Modena
    {"nome": 'Albareto', "lat": 44.446551, "lon": 9.699977},  # Parma
    {"nome": 'Bardi', "lat": 44.632211, "lon": 9.731203},  # Parma
    {"nome": 'Bedonia', "lat": 44.503631, "lon": 9.629885},  # Parma
    {"nome": 'Berceto', "lat": 44.510608, "lon": 9.989655},  # Parma
    {"nome": 'Bore', "lat": 44.718658, "lon": 9.792074},  # Parma
    {"nome": 'Borgo Val di Taro', "lat": 44.487053, "lon": 9.765777},  # Parma
    {"nome": 'Busseto', "lat": 44.980567, "lon": 10.042075},  # Parma
    {"nome": 'Calestano', "lat": 44.6007, "lon": 10.122994},  # Parma
    {"nome": 'Collecchio', "lat": 44.75202, "lon": 10.216691},  # Parma
    {"nome": 'Colorno', "lat": 44.926532, "lon": 10.377117},  # Parma
    {"nome": 'Compiano', "lat": 44.496049, "lon": 9.662079},  # Parma
    {"nome": 'Corniglio', "lat": 44.4759, "lon": 10.088926},  # Parma
    {"nome": 'Felino', "lat": 44.696587, "lon": 10.237205},  # Parma
    {"nome": 'Fidenza', "lat": 44.866522, "lon": 10.061194},  # Parma
    {"nome": 'Fontanellato', "lat": 44.881666, "lon": 10.172179},  # Parma
    {"nome": 'Fontevivo', "lat": 44.858765, "lon": 10.17518},  # Parma
    {"nome": 'Fornovo di Taro', "lat": 44.691962, "lon": 10.100985},  # Parma
    {"nome": 'Langhirano', "lat": 44.613064, "lon": 10.266114},  # Parma
    {"nome": "Lesignano de' Bagni", "lat": 44.643428, "lon": 10.300484},  # Parma
    {"nome": 'Medesano', "lat": 44.756699, "lon": 10.140737},  # Parma
    {"nome": 'Monchio delle Corti', "lat": 44.412955, "lon": 10.12419},  # Parma
    {"nome": 'Montechiarugolo', "lat": 44.693401, "lon": 10.42248},  # Parma
    {"nome": 'Neviano degli Arduini', "lat": 44.582365, "lon": 10.315689},  # Parma
    {"nome": 'Noceto', "lat": 44.809901, "lon": 10.17726},  # Parma
    {"nome": 'Palanzano', "lat": 44.437437, "lon": 10.194462},  # Parma
    {"nome": 'Parma', "lat": 44.801368, "lon": 10.328083},  # Parma
    {"nome": 'Pellegrino Parmense', "lat": 44.733168, "lon": 9.927556},  # Parma
    {"nome": 'Polesine Zibello', "lat": 45.010809, "lon": 10.098713},  # Parma
    {"nome": 'Roccabianca', "lat": 45.009369, "lon": 10.219378},  # Parma
    {"nome": 'Sala Baganza', "lat": 44.71395, "lon": 10.226334},  # Parma
    {"nome": 'Salsomaggiore Terme', "lat": 44.814762, "lon": 9.979576},  # Parma
    {"nome": 'San Secondo Parmense', "lat": 44.92016, "lon": 10.229558},  # Parma
    {"nome": 'Sissa Trecasali', "lat": 44.945594, "lon": 10.279947},  # Parma
    {"nome": 'Solignano', "lat": 44.613057, "lon": 9.97548},  # Parma
    {"nome": 'Soragna', "lat": 44.926266, "lon": 10.124178},  # Parma
    {"nome": 'Sorbolo Mezzani', "lat": 44.900097, "lon": 10.44216},  # Parma
    {"nome": 'Terenzo', "lat": 44.609958, "lon": 10.089682},  # Parma
    {"nome": 'Tizzano Val Parma', "lat": 44.520544, "lon": 10.199199},  # Parma
    {"nome": 'Tornolo', "lat": 44.485251, "lon": 9.625274},  # Parma
    {"nome": 'Torrile', "lat": 44.89885, "lon": 10.339497},  # Parma
    {"nome": 'Traversetolo', "lat": 44.639212, "lon": 10.381795},  # Parma
    {"nome": 'Valmozzola', "lat": 44.585508, "lon": 9.869151},  # Parma
    {"nome": "Varano de' Melegari", "lat": 44.68806, "lon": 10.011479},  # Parma
    {"nome": 'Varsi', "lat": 44.662157, "lon": 9.846676},  # Parma
    {"nome": 'Agazzano', "lat": 44.94437, "lon": 9.517685},  # Piacenza
    {"nome": 'Alseno', "lat": 44.898364, "lon": 9.961374},  # Piacenza
    {"nome": 'Alta Val Tidone', "lat": 44.919977, "lon": 9.347182},  # Piacenza
    {"nome": 'Besenzone', "lat": 44.986432, "lon": 9.954909},  # Piacenza
    {"nome": 'Bettola', "lat": 44.776446, "lon": 9.60628},  # Piacenza
    {"nome": 'Bobbio', "lat": 44.769273, "lon": 9.386281},  # Piacenza
    {"nome": 'Borgonovo Val Tidone', "lat": 45.014362, "lon": 9.443461},  # Piacenza
    {"nome": 'Cadeo', "lat": 44.975365, "lon": 9.83107},  # Piacenza
    {"nome": 'Calendasco', "lat": 45.087265, "lon": 9.596063},  # Piacenza
    {"nome": 'Caorso', "lat": 45.0496, "lon": 9.874375},  # Piacenza
    {"nome": 'Carpaneto Piacentino', "lat": 44.915212, "lon": 9.788095},  # Piacenza
    {"nome": 'Castel San Giovanni', "lat": 45.059067, "lon": 9.434178},  # Piacenza
    {"nome": "Castell'Arquato", "lat": 44.85118, "lon": 9.867118},  # Piacenza
    {"nome": 'Castelvetro Piacentino', "lat": 45.100335, "lon": 9.986716},  # Piacenza
    {"nome": 'Cerignale', "lat": 44.677853, "lon": 9.350765},  # Piacenza
    {"nome": 'Coli', "lat": 44.744756, "lon": 9.414565},  # Piacenza
    {"nome": 'Corte Brugnatella', "lat": 44.715767, "lon": 9.360289},  # Piacenza
    {"nome": 'Cortemaggiore', "lat": 44.996353, "lon": 9.931072},  # Piacenza
    {"nome": 'Farini', "lat": 44.712837, "lon": 9.569286},  # Piacenza
    {"nome": 'Ferriere', "lat": 44.644144, "lon": 9.497457},  # Piacenza
    {"nome": "Fiorenzuola d'Arda", "lat": 44.928084, "lon": 9.910722},  # Piacenza
    {"nome": 'Gazzola', "lat": 44.960062, "lon": 9.548964},  # Piacenza
    {"nome": 'Gossolengo', "lat": 44.99996, "lon": 9.613788},  # Piacenza
    {"nome": 'Gragnano Trebbiense', "lat": 45.013163, "lon": 9.568964},  # Piacenza
    {"nome": 'Gropparello', "lat": 44.832706, "lon": 9.729894},  # Piacenza
    {"nome": "Lugagnano Val d'Arda", "lat": 44.821994, "lon": 9.828482},  # Piacenza
    {"nome": "Monticelli d'Ongina", "lat": 45.089511, "lon": 9.933165},  # Piacenza
    {"nome": 'Morfasso', "lat": 44.722749, "lon": 9.702861},  # Piacenza
    {"nome": 'Ottone', "lat": 44.623249, "lon": 9.33266},  # Piacenza
    {"nome": 'Piacenza', "lat": 44.847635, "lon": 9.666531},  # Piacenza
    {"nome": 'Pianello Val Tidone', "lat": 44.94666, "lon": 9.405161},  # Piacenza
    {"nome": 'Piozzano', "lat": 44.926261, "lon": 9.496064},  # Piacenza
    {"nome": 'Podenzano', "lat": 44.956363, "lon": 9.684967},  # Piacenza
    {"nome": "Ponte dell'Olio", "lat": 44.867361, "lon": 9.642268},  # Piacenza
    {"nome": 'Pontenure', "lat": 44.999165, "lon": 9.788569},  # Piacenza
    {"nome": 'Rivergaro', "lat": 44.910251, "lon": 9.596521},  # Piacenza
    {"nome": 'Rottofreno', "lat": 45.057464, "lon": 9.547763},  # Piacenza
    {"nome": 'San Giorgio Piacentino', "lat": 44.954163, "lon": 9.736869},  # Piacenza
    {"nome": 'San Pietro in Cerro', "lat": 45.021367, "lon": 9.949872},  # Piacenza
    {"nome": 'Sarmato', "lat": 45.059814, "lon": 9.491398},  # Piacenza
    {"nome": 'Travo', "lat": 44.86246, "lon": 9.545366},  # Piacenza
    {"nome": 'Vernasca', "lat": 44.799438, "lon": 9.830668},  # Piacenza
    {"nome": 'Vigolzone', "lat": 44.914662, "lon": 9.669368},  # Piacenza
    {"nome": "Villanova sull'Arda", "lat": 45.026005, "lon": 9.997061},  # Piacenza
    {"nome": 'Zerba', "lat": 44.666153, "lon": 9.287164},  # Piacenza
    {"nome": 'Ziano Piacentino', "lat": 45.001462, "lon": 9.40156},  # Piacenza
    {"nome": 'Alfonsine', "lat": 44.505385, "lon": 12.04118},  # Ravenna
    {"nome": 'Bagnacavallo', "lat": 44.416148, "lon": 11.97655},  # Ravenna
    {"nome": 'Bagnara di Romagna', "lat": 44.389169, "lon": 11.826451},  # Ravenna
    {"nome": 'Brisighella', "lat": 44.22214, "lon": 11.772226},  # Ravenna
    {"nome": 'Casola Valsenio', "lat": 44.223187, "lon": 11.62311},  # Ravenna
    {"nome": 'Castel Bolognese', "lat": 44.320487, "lon": 11.799258},  # Ravenna
    {"nome": 'Cervia', "lat": 44.260984, "lon": 12.349546},  # Ravenna
    {"nome": 'Conselice', "lat": 44.512758, "lon": 11.829174},  # Ravenna
    {"nome": 'Cotignola', "lat": 44.383843, "lon": 11.940217},  # Ravenna
    {"nome": 'Faenza', "lat": 44.285556, "lon": 11.883205},  # Ravenna
    {"nome": 'Fusignano', "lat": 44.467503, "lon": 11.960192},  # Ravenna
    {"nome": 'Lugo', "lat": 44.418878, "lon": 11.908231},  # Ravenna
    {"nome": 'Massa Lombarda', "lat": 44.447386, "lon": 11.826091},  # Ravenna
    {"nome": 'Ravenna', "lat": 44.364061, "lon": 12.059009},  # Ravenna
    {"nome": 'Riolo Terme', "lat": 44.277084, "lon": 11.72452},  # Ravenna
    {"nome": 'Russi', "lat": 44.370054, "lon": 12.030417},  # Ravenna
    {"nome": "Sant'Agata sul Santerno", "lat": 44.44217, "lon": 11.860124},  # Ravenna
    {"nome": 'Solarolo', "lat": 44.360368, "lon": 11.844525},  # Ravenna
    {"nome": 'Albinea', "lat": 44.620182, "lon": 10.601926},  # Reggio nell'Emilia
    {"nome": 'Bagnolo in Piano', "lat": 44.762967, "lon": 10.673293},  # Reggio nell'Emilia
    {"nome": 'Baiso', "lat": 44.496827, "lon": 10.601678},  # Reggio nell'Emilia
    {"nome": 'Bibbiano', "lat": 44.66343, "lon": 10.472719},  # Reggio nell'Emilia
    {"nome": 'Boretto', "lat": 44.90187, "lon": 10.554287},  # Reggio nell'Emilia
    {"nome": 'Brescello', "lat": 44.901224, "lon": 10.515728},  # Reggio nell'Emilia
    {"nome": 'Cadelbosco di Sopra', "lat": 44.763319, "lon": 10.595182},  # Reggio nell'Emilia
    {"nome": 'Campagnola Emilia', "lat": 44.83927, "lon": 10.757093},  # Reggio nell'Emilia
    {"nome": 'Campegine', "lat": 44.781734, "lon": 10.528419},  # Reggio nell'Emilia
    {"nome": 'Canossa', "lat": 44.55356, "lon": 10.411914},  # Reggio nell'Emilia
    {"nome": 'Carpineti', "lat": 44.455961, "lon": 10.519698},  # Reggio nell'Emilia
    {"nome": 'Casalgrande', "lat": 44.588009, "lon": 10.737321},  # Reggio nell'Emilia
    {"nome": 'Casina', "lat": 44.51026, "lon": 10.501593},  # Reggio nell'Emilia
    {"nome": 'Castellarano', "lat": 44.510281, "lon": 10.72864},  # Reggio nell'Emilia
    {"nome": 'Castelnovo di Sotto', "lat": 44.810863, "lon": 10.563295},  # Reggio nell'Emilia
    {"nome": "Castelnovo ne' Monti", "lat": 44.435562, "lon": 10.403295},  # Reggio nell'Emilia
    {"nome": 'Cavriago', "lat": 44.694194, "lon": 10.526898},  # Reggio nell'Emilia
    {"nome": 'Correggio', "lat": 44.770561, "lon": 10.779783},  # Reggio nell'Emilia
    {"nome": 'Fabbrico', "lat": 44.872091, "lon": 10.810062},  # Reggio nell'Emilia
    {"nome": 'Gattatico', "lat": 44.795684, "lon": 10.444557},  # Reggio nell'Emilia
    {"nome": 'Gualtieri', "lat": 44.905013, "lon": 10.630226},  # Reggio nell'Emilia
    {"nome": 'Guastalla', "lat": 44.921152, "lon": 10.653998},  # Reggio nell'Emilia
    {"nome": 'Luzzara', "lat": 44.961655, "lon": 10.687068},  # Reggio nell'Emilia
    {"nome": 'Montecchio Emilia', "lat": 44.700451, "lon": 10.446413},  # Reggio nell'Emilia
    {"nome": 'Novellara', "lat": 44.84297, "lon": 10.727492},  # Reggio nell'Emilia
    {"nome": 'Poviglio', "lat": 44.84167, "lon": 10.541615},  # Reggio nell'Emilia
    {"nome": 'Quattro Castella', "lat": 44.635605, "lon": 10.472425},  # Reggio nell'Emilia
    {"nome": "Reggio nell'Emilia", "lat": 44.608664, "lon": 10.594797},  # Reggio nell'Emilia
    {"nome": 'Reggiolo', "lat": 44.918534, "lon": 10.804211},  # Reggio nell'Emilia
    {"nome": 'Rio Saliceto', "lat": 44.81001, "lon": 10.803099},  # Reggio nell'Emilia
    {"nome": 'Rolo', "lat": 44.886656, "lon": 10.858208},  # Reggio nell'Emilia
    {"nome": 'Rubiera', "lat": 44.653201, "lon": 10.781081},  # Reggio nell'Emilia
    {"nome": 'San Martino in Rio', "lat": 44.732802, "lon": 10.783988},  # Reggio nell'Emilia
    {"nome": "San Polo d'Enza", "lat": 44.625862, "lon": 10.422287},  # Reggio nell'Emilia
    {"nome": "Sant'Ilario d'Enza", "lat": 44.760505, "lon": 10.449892},  # Reggio nell'Emilia
    {"nome": 'Scandiano', "lat": 44.599345, "lon": 10.687838},  # Reggio nell'Emilia
    {"nome": 'Toano', "lat": 44.376357, "lon": 10.561197},  # Reggio nell'Emilia
    {"nome": 'Ventasso', "lat": 44.36883, "lon": 10.26931},  # Reggio nell'Emilia
    {"nome": 'Vetto', "lat": 44.484158, "lon": 10.33749},  # Reggio nell'Emilia
    {"nome": 'Vezzano sul Crostolo', "lat": 44.602584, "lon": 10.546853},  # Reggio nell'Emilia
    {"nome": 'Viano', "lat": 44.543729, "lon": 10.62162},  # Reggio nell'Emilia
    {"nome": 'Villa Minozzo', "lat": 44.365056, "lon": 10.465995},  # Reggio nell'Emilia
    {"nome": 'Bellaria-Igea Marina', "lat": 44.142108, "lon": 12.469694},  # Rimini
    {"nome": 'Casteldelci', "lat": 43.79115, "lon": 12.155126},  # Rimini
    {"nome": 'Cattolica', "lat": 43.963993, "lon": 12.744152},  # Rimini
    {"nome": 'Coriano', "lat": 43.965186, "lon": 12.602374},  # Rimini
    {"nome": 'Gemmano', "lat": 43.904162, "lon": 12.583058},  # Rimini
    {"nome": 'Maiolo', "lat": 43.87396, "lon": 12.311144},  # Rimini
    {"nome": 'Misano Adriatico', "lat": 43.977495, "lon": 12.698695},  # Rimini
    {"nome": 'Mondaino', "lat": 43.855706, "lon": 12.671392},  # Rimini
    {"nome": 'Montecopiolo', "lat": 43.841314, "lon": 12.370434},  # Rimini
    {"nome": 'Montefiore Conca', "lat": 43.890236, "lon": 12.610315},  # Rimini
    {"nome": 'Montegridolfo', "lat": 43.859, "lon": 12.688511},  # Rimini
    {"nome": 'Montescudo-Monte Colombo', "lat": 43.931376, "lon": 12.54223},  # Rimini
    {"nome": 'Morciano di Romagna', "lat": 43.913748, "lon": 12.646067},  # Rimini
    {"nome": 'Novafeltria', "lat": 43.895727, "lon": 12.290398},  # Rimini
    {"nome": 'Pennabilli', "lat": 43.817054, "lon": 12.265276},  # Rimini
    {"nome": 'Poggio Torriana', "lat": 43.999848, "lon": 12.39266},  # Rimini
    {"nome": 'Riccione', "lat": 43.998274, "lon": 12.647437},  # Rimini
    {"nome": 'Rimini', "lat": 43.947098, "lon": 12.630769},  # Rimini
    {"nome": 'Saludecio', "lat": 43.872685, "lon": 12.668507},  # Rimini
    {"nome": 'San Clemente', "lat": 43.932205, "lon": 12.625693},  # Rimini
    {"nome": 'San Giovanni in Marignano', "lat": 43.938668, "lon": 12.712494},  # Rimini
    {"nome": 'San Leo', "lat": 43.896961, "lon": 12.343644},  # Rimini
    {"nome": "Sant'Agata Feltria", "lat": 43.864302, "lon": 12.207999},  # Rimini
    {"nome": 'Santarcangelo di Romagna', "lat": 44.063382, "lon": 12.446702},  # Rimini
    {"nome": 'Sassofeltrio', "lat": 43.891962, "lon": 12.510183},  # Rimini
    {"nome": 'Talamello', "lat": 43.90499, "lon": 12.285898},  # Rimini
    {"nome": 'Verucchio', "lat": 43.983611, "lon": 12.421163},  # Rimini
]

# Scala qualitativa di intensità percepita da PGA (in g) — soglie
# indicative comunemente citate in letteratura per la scala Mercalli
# modificata approssimata, NON un rigore scientifico da bollettino
# ufficiale: usata solo per dare un'etichetta leggibile a un operatore,
# mai come sostituto di un rilievo macrosismico reale (quello lo fa solo
# l'INGV dopo l'evento).
_SOGLIE_INTENSITA_PGA_G = [
    (0.005, "non avvertito"),
    (0.02, "avvertito lievemente"),
    (0.05, "avvertito, oggetti oscillano"),
    (0.10, "avvertito diffusamente, possibili danni lievi a strutture vulnerabili"),
    (0.20, "danni moderati possibili"),
    (0.35, "danni significativi possibili"),
]


def _etichetta_intensita(pga_g: float) -> str:
    etichetta = "danni gravi possibili"
    for soglia, nome in _SOGLIE_INTENSITA_PGA_G:
        if pga_g < soglia:
            etichetta = nome
            break
    return etichetta


def stima_impatto_su_comuni(magnitudo: float, lat_epicentro: float, lon_epicentro: float, profondita_km: float | None, comuni: list | None = None, raggio_massimo_km: float = RAGGIO_MASSIMO_KM) -> list:
    """
    Per ogni comune di riferimento ENTRO raggio_massimo_km dall'epicentro,
    calcola distanza, PGA stimata (via il modello di attenuazione già
    validato-da-validare in attenuation.py) ed etichetta qualitativa —
    ordinato dal più esposto al meno esposto, pronto per guidare le prime
    ispezioni. Un comune oltre raggio_massimo_km viene escluso invece di
    essere mostrato come "esposto" con una PGA trascurabile: il catalogo
    è circoscritto all'area ferrarese (vedi nota sopra), quindi un evento
    lontano semplicemente non ha nessun comune di riferimento pertinente —
    lista vuota, mai un dato fuorviante per riempirla comunque.
    """
    comuni = comuni or COMUNI_EMILIA_ROMAGNA
    risultati = []
    for comune in comuni:
        distanza_km = haversine_km(lat_epicentro, lon_epicentro, comune["lat"], comune["lon"])
        if distanza_km > raggio_massimo_km:
            continue
        pga_g = stima_intensita_pga(magnitudo, distanza_km, profondita_km)
        risultati.append({
            "comune": comune["nome"],
            "distanza_epicentro_km": round(distanza_km, 1),
            "pga_stimata_g": round(pga_g, 4),
            "intensita_percepita_stimata": _etichetta_intensita(pga_g),
        })
    risultati.sort(key=lambda r: r["pga_stimata_g"], reverse=True)
    return risultati


# --- Legge di Omori-Utsu — parametri DA VALIDARE (vedi docstring modulo) ---
OMORI_K = float(os.environ.get("SDREA_OMORI_K", "1.0"))  # DA VALIDARE — produttività della sequenza
OMORI_C = float(os.environ.get("SDREA_OMORI_C_GIORNI", "0.05"))  # DA VALIDARE — giorni, evita singolarità a t=0
OMORI_P = float(os.environ.get("SDREA_OMORI_P", "1.05"))  # DA VALIDARE — tipicamente vicino a 1


def stima_probabilita_repliche(magnitudo_evento_principale: float, ore_trascorse: float, finestra_ore: float = 24.0) -> dict:
    """
    Tasso atteso di repliche nella finestra [ore_trascorse, ore_trascorse+finestra_ore]
    secondo Omori-Utsu: n(t) = K / (t + c)^p (t in giorni).
    Integra numericamente su un passo fine invece di usare la primitiva
    analitica esatta (identica per p≈1 vs p=1: evitare un ramo di codice
    speciale per p=1 in cambio di un errore numerico trascurabile a questa
    risoluzione).

    Ritorna il numero atteso di repliche di magnitudo pari o superiore a
    (magnitudo_evento_principale - 1.0) — soglia standard "M-1" usata in
    letteratura per le repliche più significative — nella finestra data.
    Il numero atteso, non una probabilità 0-1 diretta, viene poi convertito
    in probabilità di ALMENO UNA replica assumendo un processo di Poisson
    (approssimazione standard per sequenze sismiche).
    """
    t0_giorni = ore_trascorse / 24.0
    t1_giorni = (ore_trascorse + finestra_ore) / 24.0
    passi = 200
    dt = (t1_giorni - t0_giorni) / passi
    numero_atteso = 0.0
    for i in range(passi):
        t = t0_giorni + (i + 0.5) * dt
        numero_atteso += (OMORI_K / ((t + OMORI_C) ** OMORI_P)) * dt

    probabilita_almeno_una = 1 - math.exp(-numero_atteso)

    return {
        "magnitudo_soglia_replica": round(magnitudo_evento_principale - 1.0, 1),
        "finestra_ore": finestra_ore,
        "numero_atteso_repliche": round(numero_atteso, 3),
        "probabilita_almeno_una_replica_pct": round(probabilita_almeno_una * 100, 1),
        "modello": "Omori-Utsu (n(t) = K / (t + c)^p), parametri generici di letteratura — DA VALIDARE su questa sequenza specifica",
        "nota": (
            "Questa è una stima statistica generica del decadimento delle repliche nel tempo, "
            "non una previsione di un evento specifico: nessun modello scientifico può prevedere "
            "se, dove o quando avverrà una singola replica. Utile solo come indicazione di massima "
            "sul livello di allerta operativa nelle ore successive a un evento principale."
        ),
    }
