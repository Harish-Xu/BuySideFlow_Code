# Reference Program Table Usage

- catalog: `sweagent/text2sql/assets/gildata_table_catalog.json`
- references: `data/dataset/results`
- reference files scanned: 716
- tasks with reference files: 404
- catalog tables: 108
- used by references: 78
- not used by references: 30

## Used Tables

| table_name | reference_task_count | sample_task_ids |
| --- | ---: | --- |
| `secumain` | 275 | bond_088, fund_001, fund_002, fund_003, fund_004, fund_006, fund_007, fund_008, fund_009, fund_010, fund_011, fund_012 ... |
| `mf_fundarchives` | 102 | bond_056, bond_057, fund_001, fund_002, fund_004, fund_005, fund_006, fund_007, fund_008, fund_009, fund_010, fund_011 ... |
| `qt_tradingdaynew` | 102 | bond_009, bond_014, bond_050, bond_052, bond_056, bond_057, bond_062, bond_063, bond_064, bond_089, bond_090, fund_002 ... |
| `lc_exgindustry` | 77 | fund_002, fund_053, fund_060, fund_092, fund_093, fund_108, fund_114, fund_121, stock_001, stock_002, stock_004, stock_005 ... |
| `lc_mainindexnew` | 69 | bond_016, bond_042, stock_001, stock_002, stock_005, stock_006, stock_007, stock_010, stock_011, stock_013, stock_022, stock_023 ... |
| `mf_fundnetvalueretrans` | 63 | bond_056, bond_057, fund_003, fund_004, fund_006, fund_007, fund_008, fund_009, fund_010, fund_011, fund_012, fund_014 ... |
| `cs_riskalert` | 62 | stock_001, stock_009, stock_012, stock_013, stock_015, stock_016, stock_017, stock_018, stock_021, stock_022, stock_023, stock_024 ... |
| `lc_dindicesforvaluation` | 59 | fund_002, fund_015, stock_003, stock_005, stock_009, stock_010, stock_011, stock_012, stock_013, stock_014, stock_016, stock_017 ... |
| `qt_dailyquote` | 58 | fund_025, fund_027, fund_049, fund_060, fund_108, stock_003, stock_005, stock_009, stock_015, stock_022, stock_026, stock_027 ... |
| `mf_jyfundtype` | 54 | bond_056, bond_057, fund_002, fund_004, fund_006, fund_007, fund_009, fund_010, fund_011, fund_012, fund_014, fund_016 ... |
| `qt_indexquote` | 51 | fund_014, fund_017, fund_020, fund_024, fund_040, fund_046, fund_048, fund_050, fund_051, fund_052, fund_059, fund_060 ... |
| `bond_code` | 50 | bond_007, bond_009, bond_014, bond_015, bond_016, bond_021, bond_022, bond_023, bond_024, bond_029, bond_031, bond_035 ... |
| `lc_maindatanew` | 39 | bond_083, fund_092, stock_001, stock_003, stock_005, stock_006, stock_008, stock_012, stock_013, stock_026, stock_027, stock_040 ... |
| `bond_cbvaluationall` | 35 | bond_007, bond_009, bond_014, bond_016, bond_021, bond_022, bond_023, bond_024, bond_035, bond_040, bond_041, bond_042 ... |
| `bond_cbyieldcurve` | 32 | bond_001, bond_002, bond_003, bond_004, bond_005, bond_006, bond_007, bond_008, bond_009, bond_010, bond_011, bond_017 ... |
| `lc_indexcomponent` | 32 | stock_003, stock_005, stock_015, stock_038, stock_039, stock_045, stock_049, stock_050, stock_052, stock_053, stock_054, stock_055 ... |
| `c_ed_macroindicatordata` | 29 | bond_003, bond_010, bond_037, bond_076, bond_078, macro_001, macro_002, macro_003, macro_004, macro_005, macro_006, macro_007 ... |
| `bond_bdcreditgrading` | 26 | bond_007, bond_009, bond_014, bond_022, bond_030, bond_031, bond_036, bond_041, bond_043, bond_046, bond_050, bond_051 ... |
| `lc_stibexgindustry` | 26 | fund_002, fund_053, fund_060, fund_114, stock_024, stock_025, stock_027, stock_028, stock_030, stock_040, stock_041, stock_047 ... |
| `qt_performancedata` | 25 | stock_001, stock_003, stock_005, stock_009, stock_015, stock_018, stock_021, stock_081, stock_084, stock_087, stock_088, stock_098 ... |
| `mf_fundmanagernew` | 24 | fund_001, fund_007, fund_014, fund_020, fund_028, fund_033, fund_040, fund_041, fund_042, fund_057, fund_058, fund_066 ... |
| `lc_qfinancialindexnew` | 23 | stock_009, stock_025, stock_026, stock_027, stock_041, stock_042, stock_046, stock_048, stock_049, stock_052, stock_061, stock_063 ... |
| `lc_stibmainindex` | 22 | stock_024, stock_025, stock_028, stock_030, stock_040, stock_047, stock_048, stock_049, stock_056, stock_062, stock_063, stock_064 ... |
| `mf_scaleanalysis` | 20 | fund_013, fund_019, fund_033, fund_034, fund_047, fund_048, fund_062, fund_065, fund_070, fund_073, fund_074, fund_083 ... |
| `bond_basicquote` | 18 | bond_009, bond_014, bond_024, bond_044, bond_050, bond_052, bond_058, bond_059, bond_061, bond_062, bond_063, bond_064 ... |
| `lc_balancesheetall` | 16 | bond_083, stock_002, stock_004, stock_006, stock_012, stock_014, stock_026, stock_040, stock_051, stock_054, stock_056, stock_057 ... |
| `fin_derivative` | 14 | stock_008, stock_014, stock_016, stock_017, stock_018, stock_024, stock_027, stock_028, stock_038, stock_039, stock_041, stock_051 ... |
| `bond_conbdexchangequote` | 13 | bond_054, bond_055, bond_067, bond_068, bond_069, bond_070, bond_071, bond_073, bond_074, bond_075, bond_088, bond_093 ... |
| `lc_indexbasicinfo` | 12 | fund_111, fund_117, stock_032, stock_069, stock_097, stock_100, stock_101, stock_106, stock_107, stock_128, stock_129, stock_137 |
| `bond_baseratereference` | 11 | bond_019, bond_024, bond_026, bond_027, bond_034, bond_049, bond_061, bond_066, bond_077, bond_080, stock_146 |
| `bond_conbdbasicinfo` | 11 | bond_067, bond_068, bond_070, bond_071, bond_073, bond_074, bond_075, bond_088, bond_093, bond_094, stock_019 |
| `bond_issuenew` | 10 | bond_012, bond_028, bond_029, bond_032, bond_033, bond_052, bond_060, bond_085, bond_086, bond_087 |
| `lc_suspendresumption` | 10 | stock_015, stock_021, stock_022, stock_023, stock_159, stock_160, stock_161, stock_162, stock_163, stock_164 |
| `mf_keystockportfolio` | 10 | fund_015, fund_021, fund_022, fund_023, fund_025, fund_032, fund_047, fund_088, fund_089, fund_090 |
| `bond_basicinfo` | 8 | bond_009, bond_013, bond_030, bond_036, bond_040, bond_060, bond_084, bond_091 |
| `bond_default` | 8 | bond_009, bond_014, bond_031, bond_041, bond_062, bond_066, bond_081, bond_089 |
| `lc_dividend` | 8 | fund_025, stock_013, stock_084, stock_090, stock_158, stock_159, stock_160, stock_163 |
| `qt_fundsperformancehis` | 8 | fund_076, stock_073, stock_085, stock_151, stock_153, stock_157, stock_164, stock_165 |
| `bond_conbdcallinfo` | 7 | bond_068, bond_070, bond_073, bond_074, bond_088, bond_093, bond_094 |
| `bond_conceptnature` | 7 | bond_007, bond_009, bond_022, bond_046, bond_050, bond_064, bond_092 |
| `mf_investadvisoroutline` | 7 | fund_004, fund_020, fund_041, fund_058, fund_065, fund_066, fund_068 |
| `mf_stockportfoliodetail` | 7 | fund_002, fund_005, fund_010, fund_049, fund_060, fund_108, fund_114 |
| `bond_biindustry` | 6 | bond_040, bond_058, bond_060, bond_083, bond_089, bond_090 |
| `bond_size` | 6 | bond_012, bond_047, bond_084, bond_088, bond_093, bond_094 |
| `mf_holderinfo` | 6 | fund_019, fund_037, fund_043, fund_081, fund_083, fund_085 |
| `mf_personalinfo` | 6 | fund_033, fund_041, fund_057, fund_058, fund_078, fund_080 |
| `bond_chinabondindexquote` | 5 | bond_034, bond_045, bond_048, bond_049, bond_053 |
| `bond_indexbasicinfo` | 5 | bond_034, bond_045, bond_048, bond_049, bond_053 |
| `mf_benchmarkallocation` | 5 | fund_040, fund_091, fund_112, fund_113, fund_117 |
| `mf_portfoliodetailsall` | 5 | fund_027, fund_053, fund_092, fund_093, fund_099 |
| `mf_purchaseandredeem` | 5 | fund_031, fund_039, fund_063, fund_084, fund_086 |
| `bond_comcreditgrading` | 4 | bond_015, bond_023, bond_083, bond_089 |
| `lc_indexcomponentsweight` | 4 | fund_060, fund_114, fund_121, stock_069 |
| `mf_benchmarkgrowthrate` | 4 | fund_014, fund_016, fund_116, fund_119 |
| `bond_conbdconvertprice` | 3 | bond_055, bond_072, bond_075 |
| `hk_secumain` | 3 | fund_025, fund_032, fund_088 |
| `lc_auditopinion` | 3 | bond_041, stock_133, stock_159 |
| `mf_fundtradeinfo` | 3 | fund_019, fund_038, fund_077 |
| `qt_performance` | 3 | stock_021, stock_080, stock_108 |
| `bond_conbdputinfo` | 2 | bond_071, bond_074 |
| `bond_sapyieldcurve` | 2 | bond_038, stock_149 |
| `lc_derivativedata` | 2 | stock_012, stock_038 |
| `lc_indexderivative` | 2 | bond_002, stock_129 |
| `mf_shinfluence` | 2 | fund_054, fund_087 |
| `qt_pricelimit` | 2 | stock_119, stock_120 |
| `c_ed_indicatormain` | 1 | macro_008 |
| `ed_rmbbaseexchangerate` | 1 | bond_038 |
| `index_finindicator` | 1 | stock_129 |
| `lc_coconcept` | 1 | stock_166 |
| `lc_conceptlist` | 1 | stock_166 |
| `lc_instiarchive` | 1 | bond_015 |
| `lc_mainshlistnew` | 1 | stock_150 |
| `lc_stibdailyquote` | 1 | stock_068 |
| `mf_chargeratenew` | 1 | fund_069 |
| `mf_industryportall` | 1 | fund_120 |
| `mf_investtargetcriterion` | 1 | fund_111 |
| `qt_hkdailyquote` | 1 | fund_025 |
| `qt_osindexquote` | 1 | fund_091 |

## Unused Catalog Tables

- `bond_bondindexquote`
- `bond_creditgrading`
- `bond_repuexibquote`
- `cs_intensitytrendadj`
- `lc_business`
- `lc_competitor`
- `lc_equitypenetrate`
- `lc_mainoperincome`
- `lc_merger`
- `lc_relatedtrade`
- `lc_relationship`
- `lc_rewardstat`
- `lc_sharestru`
- `lc_stibdividend`
- `lc_stibsuspendresume`
- `lc_suppcustattach`
- `lc_suppcustdetail`
- `lc_transferplan`
- `mf_assetallocationnew`
- `mf_bondcreditgrading`
- `mf_bondportifoliodetail`
- `mf_fundportifoliodetail`
- `mf_fundprodname`
- `mf_fundtype`
- `mf_issueandlisting`
- `nq_dailyquote`
- `nq_dividend`
- `nq_exgindustry`
- `nq_mainindexnew`
- `nq_secumain`
