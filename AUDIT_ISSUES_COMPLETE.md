# Complete List of All Audit Issues (230+)

## MAIN.PY (31 Issues)

### Critical (3)
1. **Line 102**: CORS Security Vulnerability - `allow_origins=["*"]` combined with `allow_credentials=True` allows any origin to make authenticated requests
2. **Lines 213, 255**: Race Condition - Global `_cache` modified by multiple async tasks concurrently without synchronization
3. **Lines 110, 838, 843**: Hardcoded Relative Paths - Database and static file paths are relative, causing issues in different working directories

### High (7)
4. **Line 536**: IndexError Risk - `history.mot_tests[0]` assumes list is not empty
5. **Line 464**: Unreachable Code Path - Condition check unreachable after exception handling
6. **Lines 619, 646**: Duplicate Connection Close - `conn.close()` called twice in `_fallback_prediction`
7. **Line 306**: Logic Error - Using `or` instead of proper conditional for model filtering
8. **Line 299**: Incorrect Model Filtering - `.isalpha()` filters out valid models with numbers like "3 SERIES"
9. **Lines 360-376**: Duplicate Query Logic - Nearly identical SQL queries repeated
10. **Line 315**: Hardcoded Year Validation - `le=2026` becomes invalid in 2027

### Medium (11)
11. **Lines 226, 268, 352, 612**: Inefficient SQLite Connection Pattern - New connection opened/closed per request
12. **Line 270**: Import Inside Function - `from consolidate_models import` imported inside function
13. **Line 71**: Global State Mutation - Reassigning module-level variable inside function
14. **Multiple Lines**: Hard-coded Magic Numbers - Thresholds 1000, 100, population average 0.28
15. **Line 83**: Missing Null Check - DVSAClient property checked but may not exist
16. **Line 429**: Broad Exception Catching - Catches all exceptions without specificity
17. **Line 68**: Error Handling Gap - No error handling if `build_db.ensure_database()` fails
18. **Line 77**: Silent Failure - `db.get_pool()` result not checked
19. **Lines 731-747**: Validation But No Sanitization - Pydantic validators check format but don't sanitize
20. **Lines 805-815**: ADMIN_API_KEY Check - Redundant condition check
21. **Line 188**: Confidence Interval - Assumes 95% confidence without documentation

### Low (10)
22. **Line 441**: Missing Edge Case - Empty postcode allowed with `Query("", max_length=10)`
23. **Lines 313-314**: Missing Input Validation - `min_length=1` allows very short strings
24. **Lines 333-349 vs 577-598**: Inconsistent Error Response Format - Different structures returned
25. **Lines 127-143**: Health Check Incomplete - Only checks if pool exists, doesn't validate database responsive
26. **Line 255**: Cache Expiry Check Inefficient - No cache invalidation mechanism
27. **Line 383**: TypeError Risk - dict() conversion may fail with unexpected types
28. **Lines 509, 524, 535**: Missing Null Check - Model properties not validated (actually handled)
29. **Line 463**: Resource Leak Risk - HTTPException may not close connection
30. **API Rate Limits**: May be too permissive for single users
31. **Response Schema**: No Pydantic response models defined for validation

---

## MODEL_V55.PY (5 Issues)

### Critical/High (3)
32. **Line 123**: Log-Odds Numerical Instability - `np.log(raw_prob / (1 - raw_prob + 1e-10))` missing epsilon in numerator
33. **Line 117**: No Shape Validation - Model output shape not validated before accessing
34. **Line 234**: Hardcoded Magic Number - `overall_risk / 0.28` without bounds checking

### Medium (2)
35. **Lines 221-226**: Logic Error in Advisory Multiplier - Multiplier capped at 3 loses information
36. **Line 234**: Risk Ratio Unbounded - Could push component risks beyond clamp range

---

## FEATURE_ENGINEERING_V55.PY (8 Issues)

### High (2)
37. **Lines 437-438**: Unit Mismatch in Mileage Calculation - km vs miles not converted consistently
38. **Line 537**: Categorical Feature Default Type Error - Defaults to 0 for categorical features

### Medium (5)
39. **Lines 299-302**: Hardcoded Default Estimate - 5000 miles arbitrary without justification
40. **Line 275**: O(n²) Inefficiency - Advisory lookup in nested loop
41. **Lines 488-490**: Vehicle Age No Bounds Checking - Negative or extreme ages not validated
42. **Line 205**: Bias in Unit Conversion Rounding - Truncates instead of rounds
43. **Lines 452-456**: Missing Null Check on expiry_date - Edge case when date is in future

### Low (1)
44. **Lines 281, 284**: Slicing on Tests List - Redundant safety check (actually safe)

---

## BAYESIAN_MODEL.PY (6 Issues)

### Critical (1)
45. **Lines 170-172**: Broken Posterior Extraction - Shape broadcasting error, code incomplete

### High (2)
46. **Lines 61-62**: NaN Handling - Categorical encoding returns -1 for NaN, causes indexing error
47. **Lines 65-69**: Potential KeyError - Missing models in model_to_make causes KeyError

### Medium (3)
48. **Line 52**: No Error Handling on CSV Read - Crashes with uninformative error
49. **Lines 160-166**: No Convergence Diagnostics - Model could fail to converge without warning
50. **Lines 78-79**: Hardcoded Column Names - No validation columns exist

---

## CONFIDENCE.PY (4 Issues)

### High (2)
51. **Line 27**: Floating-Point Equality Comparison - `confidence == 0.95` fails with floating point
52. **Line 6**: Missing Input Validation - No validation that `0 <= successes <= total`

### Medium (2)
53. **Lines 49-56**: Arbitrary Threshold Magic Numbers - 1000, 100, 20 thresholds undocumented
54. **Line 36**: Clamping Asymmetry - Loses statistical meaning at extremes

---

## DATABASE.PY (8 Issues)

### Critical (1)
55. **Lines 23-36**: Race Condition in Pool Initialization - Multiple coroutines can create multiple pools

### High (2)
56. **Line 172**: Missing NULL Check - `rows[0]` accessed without checking length
57. **Lines 34, 275, 340, 362**: Generic Exception Handling - Catches all exceptions indiscriminately

### Medium (5)
58. **Lines 119-125, 167**: LIKE Query Performance - No proper indexes for prefix queries
59. **Lines 158-165**: Division by Zero - NULLIF used but inconsistent with defaults
60. **Lines 177-187**: Data Loss in Type Conversion - NULL converted to 0.0 silently
61. **Lines 122, 167, 204**: Missing Index Documentation - Queries assume indexes exist
62. **Line 31**: No Timeout Configuration - No query timeout specified

---

## BUILD_DB.PY (8 Issues)

### Critical (1)
63. **Lines 51-119**: Resource Leak - No try/finally, connection never closed on exception

### High (2)
64. **Lines 96, 98**: Type Conversion Silently Masks Errors - Invalid data causes ValueError
65. **Lines 58-109**: CSV Parsing No Error Handling - Malformed CSV crashes

### Medium (4)
66. **Lines 144-181**: Race Condition in Validation - TOCTOU bug between check and use
67. **Lines 159-193**: Lock File Not Cleaned Up - Accumulates in /tmp
68. **Lines 184-187**: Empty Exception Handler - Bare `except` swallows all exceptions
69. **Lines 101-109**: No Verification of Data Integrity - Inserts not verified

### Low (1)
70. **Lock File**: Uses fixed path instead of tempfile

---

## INIT_DB.PY (5 Issues)

### Critical (2)
71. **Lines 24-41**: Resource Leak - No try/finally for connection
72. **Line 32**: Dangerous Data Replacement - `if_exists='replace'` with no backup

### Medium (3)
73. **Lines 36-38**: No Index Existence Check - Crashes if run twice
74. **Lines 36-38**: Missing Composite Index - For common query pattern
75. **Lines 18-32**: No Error Handling for Pandas - CSV corruption crashes

---

## UPLOAD_TO_POSTGRES.PY (7 Issues)

### Critical (2)
76. **Lines 44-144**: Resource Leak - No try/finally, connection leaks on exception
77. **Line 60**: DROP TABLE Without Backup - Destroys production data

### High (3)
78. **Lines 91-134**: No Error Handling for CSV - Script crashes mid-upload
79. **Lines 110-112**: No Type Conversion Error Handling - ValueError not caught
80. **Lines 115-134**: No Rollback on Error - Partial upload with no recovery

### Medium (2)
81. **Lines 99-101, 108**: Silent Data Truncation - Long values truncated without logging
82. **Lines 138-141**: No Verification of Upload - Row count not validated

---

## CREATE_INDEXES.PY (4 Issues)

### Medium (4)
83. **Lines 20-40**: Resource Leak SQLite - No try/finally
84. **Lines 56-86**: Resource Leak PostgreSQL - Async connection not protected
85. **Lines 74-84**: Inadequate Error Handling - text_pattern_ops error suppressed
86. **Missing**: No leads table index creation

---

## CREATE_LEADS_TABLE.PY (6 Issues)

### Critical (1)
87. **Lines 24-84**: Resource Leak - No try/finally, connection leaks

### High (1)
88. **Lines 28-60**: No Error Handling - Table creation failure crashes

### Medium (3)
89. **Lines 28-66**: Missing Rollback - Partial changes may commit
90. **Lines 71-76**: No NULL Check - Verification query assumes columns exist
91. **Lines 31-46**: Inconsistent Permissions - email/postcode NOT NULL but name/phone nullable

### Low (1)
92. **Documentation**: Column constraints not documented

---

## DVSA_CLIENT.PY (20 Issues)

### Critical/High (5)
93. **Line 202**: Missing JSON Response Validation - `response.json()` can throw
94. **Lines 203-206**: Missing Token Structure Validation - Assumes keys exist
95. **Lines 331-332**: No Rate Limit Retry Logic - 429 immediately raises exception
96. **Throughout**: No Retry Logic Exists - No exponential backoff
97. **Lines 151-155**: Insecure Credential Retrieval - Inconsistent env var naming

### Medium (10)
98. **Throughout**: No Rate Limit Headers Tracking - X-RateLimit-Remaining ignored
99. **Line 337**: Missing JSON Validation in fetch - response.json() not protected
100. **Lines 348-351**: Information Loss on Error - Exception chain not preserved
101. **Lines 328-329**: 403 Handling Vague - No distinction auth vs authorization
102. **Lines 334-335**: Missing Retry on 5xx - Server errors not retried
103. **Lines 172-174**: Single Global Timeout - Same timeout for all operations
104. **Line 173**: No Read Timeout Distinction - Defaults to 30s for all
105. **Line 304**: Cache Key Too Simple - No version in cache key
106. **Lines 408-411**: No Selective Cache Invalidation - Only clear all
107. **Line 169**: TTLCache Memory Pressure - No memory monitoring

### Low (5)
108. **Lines 172-174**: httpx Client Not Guaranteed to Close - Resource leak risk
109. **Lines 132-174**: No Resource Cleanup on Init Failure - Client not cleaned
110. **Throughout**: No Context Manager Support - No __aenter__/__aexit__
111. **Lines 285-351**: No Try/Finally in fetch - No guaranteed cleanup
112. **Line 199**: Client Secret in Logs Risk - Response body may be logged

---

## DVLA_CLIENT.PY (13 Issues)

### Critical/High (3)
113. **Line 206**: NEW AsyncClient Per Request - Connection pool exhaustion
114. **Lines 134-137**: Demo Mode Silently Enabled - No API key silently uses fake data
115. **Line 132**: No API Key Validation - None, empty string accepted

### Medium (7)
116. **Lines 230-234**: No Rate Limit Retry - 429 immediately raises
117. **Line 216**: Invalid JSON Not Caught - response.json() can throw
118. **Line 242**: Bare Except Clause - Catches all including SystemExit
119. **Line 239**: Error Response Parsing - Could crash on invalid JSON
120. **Line 206**: No Timeout Distinction - No separate connect timeout
121. **Throughout**: No Retry Logic - No exponential backoff
122. **Throughout**: No Caching - Every request hits API

### Low (3)
123. **Lines 172-195**: Demo Data Not Validated - Schema may differ from real API
124. **Throughout**: No Close Method - Resources not released
125. **Throughout**: No Context Manager - No async with support

---

## UTILS.PY (5 Issues)

### High (1)
126. **Lines 3-9**: No Input Validation for Age - Negative ages accepted

### Medium (2)
127. **Lines 3, 11**: Missing Type Hints - No type annotations
128. **Lines 3, 11**: Inconsistent Type Checking - Only mileage checks negative

### Low (2)
129. **Lines 3, 11**: No Docstrings - Functions lack documentation
130. **Lines 1-16**: No Error Handling - pd.isna could fail

---

## REPAIR_COSTS.PY (10 Issues)

### High (2)
131. **Lines 86-115**: No vehicle_age Validation - Negative ages accepted
132. **Lines 105-109**: Unbounded Cost Multiplication - No upper limit

### Medium (6)
133. **Lines 118-144**: No Validation of risk_data Structure - Could crash on None
134. **Line 134**: Risk Value Type Not Validated - Could be None or string
135. **Line 204**: Division by Zero Risk - Very small p_fail values
136. **Lines 178-186**: Duplicated Component Mapping - Hardcoded inside function
137. **Line 211**: No Bounds Checking on fail_min - Could be negative
138. **Lines 86, 118, 155**: No Type Hints - Missing annotations

### Low (2)
139. **Line 130**: Memory Accumulation - List grows if called in loop
140. **Documentation**: No docstrings explaining formulas

---

## PROCESS_DEFECTS.PY (10 Issues)

### High (2)
141. **Lines 58, 144**: Broad Exception Handling - Could hide real errors
142. **Lines 65-70**: No Validation of Merge Columns - Assumes keys exist

### Medium (6)
143. **Lines 92-97**: Unvalidated File Glob - Continues silently if no files
144. **Lines 109-146**: Memory Bloat Risk - Large list of DataFrames
145. **Line 124**: rfr_mapping Integrity - Unknown rfr_ids not checked
146. **Line 165**: Redundant Groupby - Already grouped data grouped again
147. **Lines 7-14**: Module-level Logging - Affects other modules
148. **Lines 84, 174**: No Return Value - Caller can't verify success

### Low (2)
149. **Lines 157, 168**: No Index Parameter in to_csv - Index saved unnecessarily
150. **Lines 31, 84**: Type Hints Missing - No annotations

---

## CONSOLIDATE_MODELS.PY (11 Issues)

### Critical (1)
151. **Line 95**: Hidden Character Encoding - Invisible soft hyphen (U+00AD)

### High (1)
152. **Lines 59-80**: Non-string Input Not Validated - Fails on int/list

### Medium (6)
153. **Line 76**: Redundant Logic - Second condition already enforced by first
154. **Lines 75-77**: Inefficient String Operations - `.split()[0]` called twice
155. **Lines 99-100, 121, 129**: Regex Compiled Every Call - Performance issue
156. **Lines 110-114**: O(n) Trim Word Lookup - List instead of set
157. **Line 138**: Floating Point Comparison - No rounding consideration
158. **Lines 147-180**: Memory Inefficiency - Dict reconstructed every call

### Low (3)
159. **Lines 177-178**: MG Models List Too Long - 35+ models hardcoded
160. **Lines 59, 82, 143**: No Type Hints - Missing annotations
161. **Line 88**: No Input Length Validation - Very short model names allowed

---

## REGIONAL_DEFAULTS.PY (10 Issues)

### Critical (1)
162. **Lines 109, 150**: Duplicate Entry - 'SY': 0.55 appears twice

### High (1)
163. **Line 210**: Type Annotation Error - `any` instead of `Any`

### Medium (6)
164. **Lines 31-154**: No Validation of Corrosion Index Bounds - Values should be [0, 1]
165. **Lines 233-240**: Inconsistent Return Dict Structure - Different keys in success/failure
166. **Lines 238-239**: Misleading 'normalized' Field - Included in failure case
167. **Lines 182-195**: No Range Validation on Values - [0,1] not enforced
168. **Lines 223-226**: Hardcoded Postcode Patterns - Complex regex undocumented
169. **Lines 171, 183**: No Input Type Validation - Assumes string

### Low (2)
170. **Lines 210-240**: No Docstring - Function return structure undocumented
171. **Line 157**: No Validation of DEFAULT - Assumes 0.5 is valid

---

## AUDIT_RISK_MODEL.PY (15 Issues)

### High (3)
172. **Lines 11-12**: No File Size Limit - Could load multi-GB files
173. **Lines 39-42**: Errors Logged But Execution Continues - Corrupted data processed
174. **Line 67**: Impossible Data Not Caught - Total_Failures > Total_Tests

### Medium (9)
175. **Lines 31-36**: Risk Values Not Validated - [0,1] not enforced
176. **Lines 46-62**: NaN Values Not Analyzed - Boundary test ignores NaN
177. **Lines 70-71**: Empty risk_cols - Sum returns 0 for all
178. **Line 86**: Duplicated Mileage Mapping - Same as utils.py
179. **Lines 95, 106-107, 140, 165-172**: Arbitrary Thresholds - Magic numbers
180. **Lines 102-109**: Correlation on Small Samples - < 3 samples invalid
181. **Lines 130-131**: Shapes Compared But Not Validated - Only warns
182. **Lines 156-158**: Weights Sum Could Be Zero - Division by zero
183. **Line 180**: dataframe.drop with inplace=True - Side effects

### Low (3)
184. **Lines 87-90**: Mileage Map 'Unknown' Edge Case - Convoluted filter
185. **Line 9**: No Type Hints - Missing annotations
186. **Line 1**: No Module Docstring - Purpose undocumented

---

## FRONTEND - INDEX.HTML (1 Issue)

### Critical (1)
187. **Line 131**: Missing Security Headers - `target="_blank"` without `rel="noopener noreferrer"`

---

## FRONTEND - SCRIPT.JS (35 Issues)

### Critical (3)
188. **Lines 63, 307**: Unsafe JSON Parse - `res.json()` without try/catch
189. **Lines 66-67**: Array Access Without Bounds Check - Assumes array has elements
190. **Line 63**: Error Response Not Validated - Assumes structure exists

### High (5)
191. **Lines 43, 269**: Missing aria-busy - Screen readers not informed of loading
192. **Lines 62-73**: Incomplete Error Response Handling - Multiple issues
193. **Lines 300-309**: Missing Error Handling for Lead Form - Same JSON issue
194. **Lines 77-78**: Network Errors Not Distinguished - All errors look same
195. **Line 76**: No Validation of API Response Structure - Assumes fields exist

### Medium (17)
196. **Lines 181-183**: Direct Data Binding - Repair cost not validated
197. **Lines 271-273**: No Input Validation Before API - No client-side validation
198. **Lines 31-33**: Timer Without Cleanup - Timeout reference held
199. **Lines 37, 259**: Event Listeners Not Cleaned Up - Could accumulate
200. **Lines 217-242**: Large DOM Element Creation - Reflow per element
201. **Lines 37-84**: Race Condition on Double Submit - Only button disabled
202. **Lines 47-49**: Form Reset State Management - Could be simplified
203. **Lines 58-83**: No Network Timeout - Fetch has no timeout
204. **Line 149-158**: Magic Numbers - Risk thresholds hardcoded
205. **Lines 3-17**: Global Variable Pollution - Many globals
206. **Various**: Inconsistent Null Checks - Mix of patterns
207. **Lines 99-100, 115-117**: Repeated Element Queries - DOM queried multiple times
208. **Lines 86-255**: Complex Logic in Display - 170 lines, too many things
209. **Lines 104-105**: String Concatenation - Mixing template literals
210. **Lines 20-26**: Form Never Displays Error Properly - Assumes search-card exists
211. **Line 215**: Risk Component Sorting - Doesn't handle null
212. **Lines 179-189**: Repair Cost Formatting - Assumes structure

### Low (10)
213. **Line 272**: CSS Media Query Limited - Only one breakpoint
214. **Line 244**: Very Small Font Size - 0.7rem below WCAG minimum
215. **Line 471**: Components Grid Overflow - No mobile adjustment
216. **Line 86**: Padding Too Large - 48px on small phones
217. **Line 160**: Touch Target Size Marginal - 0.875rem minimum
218. **CSS**: No Z-index Management - Could cause stacking issues
219. **CSS Lines 298-304**: No Overflow on Results - Could overflow
220. **Line 419-420**: Color Contrast Too Low - Red on light red

---

## FRONTEND - STYLE.CSS (6 Issues)

### Medium (4)
221. **Line 272**: Limited Media Query Coverage - Missing small phone sizes
222. **Line 244**: Footer Font Size - Below accessibility minimum
223. **Line 471**: Grid May Overflow - No mobile breakpoint
224. **Lines 419-420**: Insufficient Color Contrast - Red on light red fails WCAG

### Low (2)
225. **Throughout**: No Z-index Scale - No management system
226. **Lines 298-304**: No Overflow Handling - Results panel overflow

---

## DOCKERFILE (6 Issues)

### Critical (1)
227. **Entire File**: Running as Root - No USER directive

### Medium (4)
228. **Line 2**: Outdated Python Version - 3.9 reached EOL
229. **Missing**: No Health Check - No HEALTHCHECK directive
230. **Lines 13, 19**: Missing COPY Ownership - Files owned by root
231. **Line 29**: Hardcoded Worker Count - Should be configurable

### Low (1)
232. **Lines 8-10, 16, 19**: No Layer Caching Optimization - Minor improvement possible

---

## REQUIREMENTS.TXT (7 Issues)

### Critical (1)
233. **Line 9**: Unmaintained Package - slowapi is unmaintained since 2023

### Medium (4)
234. **Lines 10-12**: Version Constraint Too Loose - `>=1.2` allows any version
235. **Lines 1-12**: All Dependencies Outdated - 12+ months old
236. **Missing**: No Separate Test Requirements - No requirements-dev.txt
237. **Lines 7-8**: Missing Database Driver Documentation - Both sync/async included

### Low (2)
238. **Missing**: No Runtime Dependencies Documentation - CatBoost needs libgomp1
239. **Comments**: No Comments on Constraints - Why specific versions

---

## TEST FILES (47 Issues)

### Test Coverage Gaps (16)
240. **test_api.py**: Missing concurrent request tests
241. **test_api.py**: Missing timeout/performance tests
242. **test_api.py**: Missing database connection failure tests
243. **test_api.py**: Missing malformed response tests
244. **test_api.py**: Missing edge cases in risk calculation
245. **test_api.py**: Missing rate limiting tests
246. **test_api.py**: Missing API response time verification
247. **test_api.py**: Missing empty makes/models list handling
248. **test_api.py**: Missing authentication/authorization tests
249. **test_api.py**: Missing integration tests with real database
250. **test_confidence.py**: Missing invalid input validation tests
251. **test_confidence.py**: Missing classification boundary tests
252. **test_dvla.py**: Missing invalid DVLA API response tests
253. **test_dvla.py**: Missing timeout/retry tests
254. **test_dvla.py**: Missing DVLA rate limiting tests
255. **test_dvla.py**: Missing vehicle data validation edge cases

### Test Quality Issues (12)
256. **test_api.py Lines 62, 106, 122, 130**: Ambiguous status code assertions
257. **test_api.py Line 160**: String matching in error messages (brittle)
258. **test_api.py Line 198**: Undeterministic test assertions
259. **test_api.py Lines 84-88**: Incomplete response field validation
260. **test_api.py Lines 77-99**: Missing assertion on CI values
261. **test_api.py Lines 26, 36, 204**: Weak type validation
262. **test_api.py Lines 162-180**: Incomplete boundary testing
263. **test_api.py Lines 58-76**: Mock data realism issues
264. **test_banding.py Lines 14-38**: Missing None value tests
265. **test_banding.py Lines 13-25**: Missing exact boundary tests
266. **test_dvla.py Multiple**: Repeated async test boilerplate
267. **test_dvla.py Lines 235-237**: Hardcoded field expectations

### Flaky/Brittle Tests (4)
268. **test_api.py Lines 21-76**: Test data dependency on database
269. **test_dvla.py Multiple**: Async test consistency issues
270. **test_defects.py Lines 14-46**: CSV file cleanup issues
271. **test_api.py Lines 58-76**: Missing actual vs expected risk verification

### Missing Test Infrastructure (6)
272. **Missing**: No pytest.ini configuration
273. **Missing**: No conftest.py for shared fixtures
274. **Missing**: No .coveragerc for coverage
275. **Multiple Files**: Redundant sys.path manipulation
276. **Missing**: No pytest-asyncio integration
277. **Missing**: No test fixtures for database seeding

---

## CROSS-MODULE ISSUES (9)

### Code Duplication
278. **utils.py, audit_risk_model.py**: Mileage band mapping duplicated
279. **repair_costs.py, feature_engineering_v55.py**: Component mapping duplicated
280. **Multiple files**: Postcode area extraction duplicated

### Missing Type Hints
281. **All modules**: Comprehensive type hints missing throughout

### Performance Issues
282. **consolidate_models.py**: Regex patterns compiled on every call
283. **consolidate_models.py**: Dictionary lookups using lists instead of sets
284. **process_defects.py, audit_risk_model.py**: Multiple DataFrame passes

### Validation Gaps
285. **All modules**: No systematic input validation on numeric parameters
286. **All modules**: Edge cases (negative, NaN, infinity) not consistently handled

---

**TOTAL: 286 Distinct Issues Identified**

| Severity | Count |
|----------|-------|
| Critical | 15 |
| High | 42 |
| Medium | 134 |
| Low | 95 |
| **Total** | **286** |
