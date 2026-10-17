Monitoramilhas — coletor inicial v1

O que esta versão faz:
- Consulta páginas oficiais de Azul Fidelidade, LATAM Pass, Smiles, Livelo e Esfera.
- Grava data/hora, status HTTP, título e pequenos trechos para conferência em:
  data/monitor_fontes.json
- Registra falhas de consulta sem esconder o resultado.
- Não adiciona campanhas automaticamente a data/promocoes.json. Isso é intencional:
  o robô ainda não deve declarar uma promoção ativa sem confirmar datas, percentual,
  regras de elegibilidade e condições de clubes com segurança.

Como instalar:
1. No GitHub, abra Monitoramilhas > monitor > monitor.py.
2. Clique no lápis (Edit) e substitua TODO o conteúdo pelo arquivo monitor.py deste pacote.
3. Commit changes.
4. Abra Actions > Monitor de promoções > Run workflow.
5. Abra a execução e confira a etapa "Executar monitor".
6. Depois do sucesso, confira se foi criado data/monitor_fontes.json.

Importante:
- O workflow atual já executa `python monitor/monitor.py` e pode continuar como está.
- O relatório de fontes é uma etapa intermediária; não é ainda um detector completo de promoções.
- Alguns sites podem bloquear consultas automáticas ou alterar seus endereços.
- Não altere a agenda de quatro execuções diárias por enquanto.
