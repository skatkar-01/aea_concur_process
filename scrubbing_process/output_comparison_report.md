# Output Comparison Report

- Reference: `scrubbing_process\_tmp_completed_inspect.xlsm`
- Output: `scrubbing_process\scrubbed_output.xlsx`

## AmEx Load Raw
- Reference data rows: 560
- Output data rows: 558
- Reference max_row/max_col: 997/15
- Output max_row/max_col: 559/15
- **Row order differs** in the first rows versus reference.
- Missing keyed rows: 2
- Extra keyed rows: 0
- Missing row examples:
  - ('', '', '', '', '', '=G561-G562', '', '', '', '')
  - ('', '', '', '', '', '=G563-G562', '', '', '', '')
- Column mismatch counts on matched keys:
  - Report Purpose: 558
    - ref row 11 -> out row 331: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '8', 'TRAVEL AGENCY SERVICES', '1035', 'CONS', 'A0CU')
    - ref row 10 -> out row 330: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 9 -> out row 329: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-23', '19.74', 'GEG GREEDY COW BURGER 698', '1035', 'CONS', 'A0CU')

## AmEx All
- Reference data rows: 563
- Output data rows: 558
- Reference max_row/max_col: 997/17
- Output max_row/max_col: 560/17
- Missing keyed rows: 5
- Extra keyed rows: 0
- Missing row examples:
  - ('', '', '', '', '', '=AEA_Posted!G396', '', '', '', '')
  - ('', '', '', '', '', '=SBF_Posted!G155', '', '', '', '')
  - ('', '', '', '', '', '=DEBT_Reviewed!G12', '', '', '', '')
  - ('', '', '', '', '', '=SUM(G562:G564)', '', '', '', '')
  - ('', '', '', '', '', '=G560-G565', '', '', '', '')
- Column mismatch counts on matched keys:
  - Report Purpose: 558
    - ref row 331 -> out row 331: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '8', 'TRAVEL AGENCY SERVICES', '1035', 'CONS', 'A0CU')
    - ref row 330 -> out row 330: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 329 -> out row 329: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-23', '19.74', 'GEG GREEDY COW BURGER 698', '1035', 'CONS', 'A0CU')
  - LEN: 558
    - ref row 331 -> out row 331: '=LEN(F331&K331)+12' != 49 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '8', 'TRAVEL AGENCY SERVICES', '1035', 'CONS', 'A0CU')
    - ref row 330 -> out row 330: '=LEN(F330&K330)+12' != 24 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 329 -> out row 329: '=LEN(F329&K329)+12' != 48 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-23', '19.74', 'GEG GREEDY COW BURGER 698', '1035', 'CONS', 'A0CU')
  - Report Entry Vendor Name: 281
    - ref row 331 -> out row 331: 'Frosch' != 'Travel Agency Services' | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '8', 'TRAVEL AGENCY SERVICES', '1035', 'CONS', 'A0CU')
    - ref row 329 -> out row 329: 'Geg Greedy Burger' != 'Geg Greedy Cow Burger 698' | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-23', '19.74', 'GEG GREEDY COW BURGER 698', '1035', 'CONS', 'A0CU')
    - ref row 328 -> out row 328: 'Delta' != 'Delta Air Lines' | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-25', '-984.41', 'DELTA AIR LINES', '1035', 'CONS', 'A0CU')
- **LEN formula/value mismatch count:** 558
- Highlight/fill mismatches by column:
  - Report Entry Description: 558
  - Report Entry Vendor Name: 558
  - LEN: 45

## AEA_Posted
- Reference data rows: 394
- Output data rows: 558
- Reference max_row/max_col: 396/16
- Output max_row/max_col: 560/16
- **Row order differs** in the first rows versus reference.
- Missing keyed rows: 191
- Extra keyed rows: 355
- Missing row examples:
  - ('Juliet', 'A', 'Yznaga', '', '2026-02-10', '-2015.09', 'Delta', '1035', 'CONS', 'A0GV')
  - ('Juliet', 'A', 'Yznaga', '', '2026-02-10', '30', 'Frosch', '1035', 'CONS', 'A0GV')
  - ('Juliet', 'A', 'Yznaga', '', '2026-02-10', '30', 'Frosch', '1035', 'CONS', 'A0GV')
  - ('Juliet', 'A', 'Yznaga', '', '2026-02-20', '11.47', '4035 Market', '1035', 'CONS', 'A0GV')
  - ('Juliet', 'A', 'Yznaga', '', '2026-02-26', '24.5', 'Travel Right', '1035', 'CONS', 'A0GV')
- Extra row examples:
  - ('Deborah', 'Marie', 'Ackerman', '', '2026-02-18', '-2097.29', 'United Airlines Arc', '1035', 'AMA', 'A0CP')
  - ('Benjamin', '', 'Althaus', '', '2026-02-10', '24.38', 'Jersey Mikes 13231', '3354', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-10', '96.87', 'Versailles Rstr', '3354', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-11', '37.98', 'Uber', '3431', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-11', '25.98', 'Uber', '3501', 'SBF', 'A09A')
- Column mismatch counts on matched keys:
  - Report Purpose: 203
    - ref row 185 -> out row 139: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 190 -> out row 144: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-26', '1059.2', 'Delta', '4253', 'CONS', 'A0CU')
    - ref row 189 -> out row 143: 'Batch # 1 - $119,802.46' != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-26', '1469.2', 'Delta', '4253', 'CONS', 'A0CU')
  - LEN: 203
    - ref row 185 -> out row 139: '=LEN(F185&J185)+12' != 24 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 190 -> out row 144: '=LEN(F190&J190)+12' != 44 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-26', '1059.2', 'Delta', '4253', 'CONS', 'A0CU')
    - ref row 189 -> out row 143: '=LEN(F189&J189)+12' != 44 | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-26', '1469.2', 'Delta', '4253', 'CONS', 'A0CU')
  - Report Entry Description: 112
    - ref row 185 -> out row 139: 'SLC-GEG/Strategy Mtg' != 'SLC-GEG' | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 193 -> out row 147: 'Bus.Parking/Strategy Mtg' != 'Airport Parking' | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-26', '62', 'Spokane Airport Parking', '1035', 'CONS', 'A0CU')
    - ref row 59 -> out row 284: 'Refund/SLC-JFK/Ski Outing/Harris Williams' != 'Refund/SLC-JFK/Harris Williams Ski Outing' | key=('Ali', 'Allexandre', 'Mehfar', '', '2026-02-10', '-324.29', 'JetBlue', '4001', 'CONS', 'A08W')
  - col15: 17
    - ref row 185 -> out row 139: "JJ: Refunded flight. Changing to 4001 QU: This is okay to keep in 1035. Both 1035 and 4001 are mgmt co exps. Only need to move refunds to 4001 & 6001 when it's either fund or portfolio related. Updated proj back to 1035" != None | key=('Adam', 'Frederick', 'Goetsch', '', '2026-02-22', '984.41', 'Delta', '1035', 'CONS', 'A0CU')
    - ref row 60 -> out row 285: 'JJ: Updated description' != None | key=('Ali', 'Allexandre', 'Mehfar', '', '2026-02-11', '5106.83', 'Carbone', '4263', 'CONS', 'A08W')
    - ref row 124 -> out row 366: 'JJ: 02/11/2026 Ride' != None | key=('Hannah', 'Beth', 'Norowitz', '', '2026-02-12', '44.98', 'Uber', '4001', 'CONS', 'A0GD')
  - Report Entry Expense Type Name: 2
    - ref row 62 -> out row 287: 'Lodging' != 'Meals' | key=('Ali', 'Allexandre', 'Mehfar', '', '2026-02-12', '816.56', 'La Baia', '4263', 'CONS', 'A08W')
    - ref row 135 -> out row 387: 'Info Services' != 'Airline' | key=('Hannah', 'Beth', 'Norowitz', '', '2026-03-02', '5', 'Delta', '4001', 'CONS', 'A0GD')
- **LEN formula/value mismatch count:** 203
- Highlight/fill mismatches by column:
  - Report Entry Description: 203
  - Report Entry Expense Type Name: 2
  - Employee ID: 2
  - Project: 1
  - Report Entry Vendor Name: 1

## SBF_Posted
- Reference data rows: 153
- Output data rows: 0
- Reference max_row/max_col: 156/16
- Output max_row/max_col: 2/16
- Missing keyed rows: 153
- Extra keyed rows: 0
- Missing row examples:
  - ('Benjamin', '', 'Althaus', '', '2026-02-05', '21.94', 'Uber', '1008', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-05', '-24.73', 'American Express', '1008', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-05', '24.73', 'Grubhub', '1008', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-06', '-14.24', 'American Express', '1008', 'SBF', 'A09A')
  - ('Benjamin', '', 'Althaus', '', '2026-02-06', '14.24', 'Grubhub', '1008', 'SBF', 'A09A')

## DEBT_Reviewed
- Reference data rows: 10
- Output data rows: 0
- Reference max_row/max_col: 12/16
- Output max_row/max_col: 2/16
- Missing keyed rows: 10
- Extra keyed rows: 0
- Missing row examples:
  - ('Thomas', 'Wilson Shaw', 'Groves', '', '2026-02-07', '24.33', "Rinaldi's Deli", '7500', 'DEBT', 'A07V')
  - ('Thomas', 'Wilson Shaw', 'Groves', '', '2026-02-07', '59.52', 'Staples', '7500', 'DEBT', 'A07V')
  - ('Thomas', 'Wilson Shaw', 'Groves', '', '2026-02-09', '849.81', 'Delta', '1035', 'DEBT', 'A07V')
  - ('Thomas', 'Wilson Shaw', 'Groves', '', '2026-02-09', '30', 'Frosch', '1035', 'DEBT', 'A07V')
  - ('Thomas', 'Wilson Shaw', 'Groves', '', '2026-02-10', '44.55', 'Chick-fil-A', '7500', 'DEBT', 'A07V')

## Entity Distribution Check
- AEA_Posted: 558 data rows
- SBF_Posted: 0 data rows
- DEBT_Reviewed: 0 data rows
