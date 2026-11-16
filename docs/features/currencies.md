# Currencies

Amounts come with their currency: a cost in EUR, a house in CHF, a crypto position in BTC.
Currencies and their exchange rates are records, and amounts are converted with the rate of the
day they're about.

## Amounts in fields

```python
"currency": Currency("Currency", required=True),
"amount": Money("Amount", currency_field="currency"),   # in the record's currency
"fee": Money("Fee", currency="CHF", scale=2),            # always in CHF
```

- `Money` is an exact number like `Decimal` (sorted, filtered and summed as a number), with its
  currency named by a `Currency` field of the same record or fixed in the declaration (one of the
  two). The app shows it with its currency: `EUR 1'250.56`.
- `Currency` holds a code in upper case: `CHF`, `EUR`, `BTC`, `USDT` (3 to 5 letters). The app
  offers the active currencies to choose from.
- A `Decimal` field can become a `Money` field on an upgrade without losing values.

## Currencies

`sys.currency`: the code, its name (translated), the decimals it's usually shown with, and
whether it's offered. Every installation starts with CHF, EUR, USD, GBP, JPY, CAD, AUD, SEK, NOK,
DKK, PLN, CZK, CNY, BTC and ETH; administrators add others and switch them off. Everyone reads
them. The setting **Main currency** (`sys.main_currency`, CHF) names the currency amounts are
shown and summed in when they come in several.

## Exchange rates

`sys.exchange_rate`: per day, what one unit of a currency is worth in a base currency
(`date`, `currency`, `base`, `rate`, and where it came from). One rate per day, currency and
base. Administrators enter them; everyone reads them.

A conversion (`Client.convert(amount, "CHF", "USD", on=date)`, exact text back) uses the latest
rate **on or before** the day, at most 10 days old: a rate between the two currencies, either
way round, or through a currency both have a rate to. Without one it's refused ("There's no
exchange rate from CHF to USD on …"), never guessed.

## The ECB's rates

The job **Load the ECB's exchange rates** reads the European Central Bank's daily rates (from
`www.ecb.europa.eu`; nothing is sent there) on working days at 16:30 Zurich time and records them
in EUR. It does nothing until the setting **Exchange rates from the ECB**
(`sys.exchange_rates_ecb`) is switched on: it's off by default, since it reaches outside the
installation.
