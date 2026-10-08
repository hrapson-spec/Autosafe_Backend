"""Frozen existing-URL content pilot. No generated expansion or new routes."""

MODEL_TEMPLATES = {
    ("vauxhall", "corsa"): "pilot_corsa.html",
    ("citroen", "c3"): "pilot_c3.html",
}

COMPARISON_TEMPLATES = {
    "renault-clio-vs-peugeot-208": "pilot_clio208.html",
    "volkswagen-polo-vs-ford-fiesta": "pilot_polofiesta.html",
    "toyota-yaris-vs-honda-jazz": "pilot_yarisjazz.html",
}

# Separate 90-day programme; frozen before edits, not the cancelled pilot.
PROGRAMME_90D_MODELS = (('vauxhall', 'adam'), ('jeep', 'compass'), ('skoda', 'scala'), ('nissan', 'micra'), ('dacia', 'duster'), ('vauxhall', 'astra'), ('jaguar', 'xf'), ('toyota', 'yaris'), ('bmw', '1-series'), ('volkswagen', 'golf'))
MODEL_TEMPLATES.update({model: "programme_model.html" for model in PROGRAMME_90D_MODELS})
