# SaaS CAS Lab

Laboratório **público, descartável e isolado** para testar persistência concorrente de um ledger via GitHub Actions. Não é o produto SaaS Salão e não tem API paga ou segredos de produção.

## Escopo do experimento

O workflow `.github/workflows/cas-proof.yml` executa apenas em `push` da `main` deste repositório. Todos os jobs executam o código da própria `main`, sem eventos de forks, PRs ou `pull_request_target`.

1. **Preflight:** 12 testes de escopo e invariantes, sem credenciais.
2. **Bootstrap:** cria referência Git exclusiva para a execução: `refs/heads/experiment/cas-probe-<run-id>-<attempt>`.
3. **Claim prepare:** dois jobs independentes criam commits irmãos, com o mesmo parent, propondo vencedores diferentes.
4. **Claim race:** dois novos jobs tentam atualizar a mesma ref via `PATCH /git/refs`, `force: false`.
5. **Claim verify:** confirma por HTTP e leitura do SHA/ledger que só um persistiu.
6. **Budget prepare/race:** dois jobs independentes disputam a primeira reserva monetária no ledger real.
7. **Budget reconcile:** um escritor único lê o ledger real, deduplica e processa 6 operações; autoriza 4 sob limite de 4. Faz uma injeção controlada de **ACK perdido** após atualização, e relê o servidor para evitar duplicata.
8. **Cleanup `always()`:** remove somente a ref efêmera do próprio run e confirma que ela deixou de existir.

## Segurança

- O GitHub Actions usa seu `GITHUB_TOKEN` efêmero com `contents: write` apenas em jobs que precisam modificar **este repositório descartável**. O token tem escopo de repositório, não de branch.
- Não usar tokens pessoais, chaves OpenAI, secrets, código de PR externo, `pull_request_target` ou merges.
- Nenhuma mutação `force:true`; atualizações não-fast-forward são esperadas como perdedoras.
- Os scripts recusam repositório ou evento inadequado e restringem todas as alterações de ref ao nome derivado dos IDs da execução.
- A `main` não é alterada por nenhuma execução do laboratório; apenas commits da montagem experimental ficam na branch efêmera, posteriormente excluída.

## Critérios

- Dois writers remotos distintos e commits irmãos com parent igual.
- Precisamente um HTTP de sucesso, outro HTTP de rejeição; confirmar SHA do vencedor e payload persistido.
- Repetir disputa com commits irmãos para a primeira reserva monetária.
- Reserva final: 4 registros únicos para teto de 4, duas operações negadas.
- Erro de ACK **simulado** após escrita + leitura remota confirma persistência; nenhum retry pago.
- Artifactos de propostas, respostas HTTP e estado final disponíveis no workflow; cleanup final concluído.

**Limites da evidência:** `PATCH force:false` não garante compare-and-swap arbitrário com SHA esperado; o teste com commits irmãos apenas prova rejeição de atualização não-fast-forward. O job de reconciliação usa **escritor único** para manter o teto, e a perda de ACK é simulada. Não afirmar transação entre duas refs, prevenção completa de overspend distribuído ou falha real de rede sem provas adicionais.

## Origem

Os primeiros experimentos, arquitetura e análise de segurança permanecem em [SaaS-Project/experiments/claim_budget_lab](https://github.com/RamonRDR/SaaS-Project/tree/experiment/claim-budget-lab/experiments/claim_budget_lab).
