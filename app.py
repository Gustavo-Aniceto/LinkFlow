"""Ponto de entrada para a Vercel (a Vercel procura a variável `app` neste arquivo).
Localmente use: python run.py"""
from linkflow import create_app

app = create_app()
