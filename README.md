# SaaS CAS Lab

Repositório **descartável e isolado** para verificar concorrência Git-ref, orçamento idempotente e recuperação de falhas com GitHub Actions.

- **Não é** o SaaS de produção nem um deploy.
- Não possui API de IA, tokens personalizados nem credenciais do projeto principal.
- O teste de escrita cria apenas refs efêmeras `experiment/cas-probe-<run-id>` e as limpa ao terminar.
- A conta padrão do GitHub Actions é a única credencial usada, com permissão `contents: write` apenas nos jobs de teste que precisam escrever.
- O fluxo não processa PRs, forks, comentários ou `pull_request_target`.
- Nenhuma execução deve alegar CAS transacional até conferir ledger e resposta real da API.
- Documentação técnica do projeto: https://github.com/RamonRDR/SaaS-Project/tree/experiment/claim-budget-lab/experiments/claim_budget_lab

**Objetivo:** obter evidência sobre `PATCH /git/refs` com `force:false`, commits irmãos, reconciliador CAS, resposta ambígua e cleanup.
