# Monitoramilhas (nome provisório)

Site estático em HTML/CSS/JavaScript, preparado para publicação no GitHub Pages.

## Arquivos
- `index.html`: página inicial
- `styles.css`: visual responsivo
- `app.js`: filtros e renderização das promoções
- `data/promocoes.json`: campanhas verificadas
- `.github/workflows/monitor.yml`: agenda de monitoramento
- `monitor/monitor.py`: base do monitor automático

## Estado atual
A interface não inventa campanhas. A integração das fontes e os alertas ainda precisam ser implementados.

## Publicação
Envie o conteúdo do ZIP para a raiz do repositório GitHub. Depois, ative GitHub Pages para publicar a partir da branch `main`, pasta `/(root)`.

## Formato esperado para cada campanha
Cada objeto de `promocoes` deve conter `id`, `origem`, `destino`, `titulo`, `bonus_percentual`, `validade`, `elegibilidade`, `url_fonte`, `fonte_nome`, `detectada_em` e `status`. Só devem ser publicadas campanhas verificadas e com link de fonte.
