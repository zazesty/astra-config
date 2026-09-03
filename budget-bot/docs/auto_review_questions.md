# Auto-review rule workshop — questions for Zavdi

Answer in any form (table, bullets, “all cafeteria = dining”). I’ll turn answers into rules.

## A. Clear recurring unknowns (high leverage)

| Merchant / label (examples) | What is it? | Category? | Always same? |
|----------------------------|-------------|-----------|--------------|
| CTLP*J AND J VENDING | vending on campus? | ? | |
| BROTHER REFRESH EZ PRI | printer ink / toner sub? | Software or Shopping? | |
| GOFNDME* SUPPORT JELLY | donation? personal? | Charity / Misc? | |
| DVC BOOKSTORE | school supplies | Shopping? | |
| DVC CAFETERIA | meals | Dining Out? | |
| LANEY COLLEGE CAFETERI | meals | Dining Out? | |
| TOO GOOD TO GO INC. | surplus food app | Dining / Groceries? | |
| OPENROUTER, INC | AI API credits | Software & Tools? | |
| X CORP. PAID FEATURES | X premium | Entertainment / Software? | |
| THORNE RESEARCH | supplements | Medical & Health? | |
| FASHION CLEANERS | dry cleaning | Personal Care? | |
| SEED.COM | ? | | |
| 001 DEBTOREDU / DEBTORCC | debt collection / edu debt? | Irregular? | |
| CORPORATE FILINGS LLC | LLC filing / legal | Irregular? | |
| POSTSCANMAIL | already Postage — confirm | | |
| NSF ACH ($14×) | bank fee | Irregular / Misc? | exclude from hardcap? |
| Partner Fee (PayPal) | FX/partner fee on payments | Misc? attach to parent purchase? | |
| VENMO NAME | friends / rent / ? | Transfer vs spend? | |
| 68188 / SPEEDWAY-ish | gas or store? | Gas / Shopping? | |
| MONUMENT 76 | restaurant/bar? | Dining Out? | |
| SQ *SHAKE SHACK | dining | Dining Out? | |
| SQ *DIVINE DOVE | ? | | |

## B. Policy

1. **School food** (DVC/Laney cafeteria): Dining Out or Groceries?
2. **AI API top-ups** (OpenRouter, Anthropic usage beyond sub): Software & Tools always?
3. **GoFundMe**: always Charity, or case-by-case?
4. **Venmo / person names**: default Transfer (exclude hardcap) or needs_review always?
5. **Bank fees (NSF)**: count toward hardcap?
6. **PayPal Partner Fee**: fold into Misc, or ignore if tiny?
7. **ATM adjustments / reverse**: Income/Transfer exclude?
8. **Amazon / eBay**: Shopping always, even small digital?
9. **Opaque POS #… / MasterMoney REF only**: leave needs_review forever, or default Misc auto-accept under $X?

## C. Hardcap inclusion (yes/no)

For each, should it **count toward the $1000 hardcap**?

- [ ] Tuition-adjacent bookstore  
- [ ] Insurance (CSAA, VSP, student)  
- [ ] Charity  
- [ ] Debt payments / IRS  
- [ ] Income-side refunds (eBay sold item)  

## D. Optional

Any merchants you **always** want human eyes on even if rules match?
Any nickname map (e.g. “68188” = Speedway gas)?
