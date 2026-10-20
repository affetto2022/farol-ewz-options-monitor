# Farol Traders — EWZ Options Monitor Online

## Arquitetura
- Flask + yfinance para consultar a cadeia atual do EWZ no Yahoo Finance.
- SQLite no disco persistente do Render (`/var/data/ewz_history.sqlite3`).
- HTTP Basic Auth para não deixar o dashboard aberto sem senha.
- Dashboard acessível por HTTPS no endereço `onrender.com`.
- Atualização manual e atualização periódica no navegador enquanto a página estiver aberta.

## Publicação pelo GitHub + Render
1. Crie um repositório **privado** no GitHub, por exemplo `farol-ewz-options-monitor`.
2. Extraia o ZIP e envie os arquivos desta pasta para a raiz do repositório (`app.py`, `index.html`, `requirements.txt`, `render.yaml`, este README).
3. Acesse https://dashboard.render.com/ e entre com GitHub.
4. Escolha **New + Blueprint** e selecione o repositório criado.
5. O Render lerá `render.yaml`. O plano `starter` e disco persistente são recursos pagos; revise os custos mostrados antes de confirmar.
6. Durante a configuração, defina `APP_USERNAME` e `APP_PASSWORD` como credenciais fortes, únicas e não reutilizadas. Não coloque senhas no código ou no GitHub.
7. Confirme o deploy. Ao concluir, abra a URL `https://...onrender.com`; o navegador pedirá usuário e senha.
8. Clique em **Atualizar agora** para registrar o primeiro snapshot. Depois, ajuste o intervalo de atualização automática.

## Histórico persistente
O banco fica em `/var/data/ewz_history.sqlite3`, no disco persistente do Render. Não remova o disco se quiser preservar as capturas. Faça exportações CSV regulares como backup.

## Segurança e privacidade
- Use um repositório GitHub privado.
- Defina APP_USERNAME e APP_PASSWORD no painel do Render.
- Não compartilhe a URL junto com as credenciais.
- O HTTP Basic Auth é protegido pelo HTTPS fornecido pelo Render; não use o app em hospedagem sem HTTPS.
- A URL poderá ser acessada de qualquer lugar, mas a aplicação exige autenticação.
- Nunca armazene segredos no repositório.

## Limitações dos dados
`yfinance` não é uma API oficial com SLA. Yahoo Finance pode atrasar, limitar ou alterar os dados e endpoints. Open Interest não necessariamente muda em cada atualização. A comparação é entre snapshots capturados pelo próprio aplicativo, não um histórico oficial intradiário do Open Interest.

## Atualização automática
A atualização automática do navegador só ocorre enquanto a página está aberta. O serviço Render precisa estar ativo; no plano configurado, não usa o modo gratuito que desliga após inatividade. A cada intervalo, o navegador solicita uma cadeia e salva snapshot no banco.
