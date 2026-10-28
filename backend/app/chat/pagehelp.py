"""Curated "Explain this page" help, in English, Hindi and Marathi.

Every sentence describes something the page really has. Each page is a list of (audience, sentence): the first
entry (audience "*") is the summary and the rest are added only for the roles they apply to, so a user is never
told about a button they do not have, and actions they cannot take are explained as unavailable to their role.

Audiences: "*" everyone, "inst" institution users, "rev" AICTE reviewers and approvers, "apr" approvers only,
"admin" administrators, "aud" auditors. The help is built in: no AI provider is involved and no record data is
read, except the status of one record the user may already see (see `status_help`).
"""

from app.models import Role

NOT_BANK_VERIFIED = {
    "en": "Nothing on this page is verified with a bank.",
    "hi": "इस पेज पर कुछ भी बैंक से सत्यापित नहीं है।",
    "mr": "या पानावरील काहीही बँकेकडून सत्यापित केलेले नाही.",
}

Help = list[tuple[str, str]]
PAGES: dict[str, dict[str, Help]] = {
    "overview": {
        "en": [
            ("*", "This overview summarises the records you can see. The cards show counts by status, the principal of live FDRs (as submitted), FDRs maturing within 30 days and FDRs past maturity; every figure is calculated by the server from current records."),
            ("*", "Needs attention lists the most urgent alerts, Recent FDRs and Recent activity show the latest records and status changes, and System status shows whether the API and database are reachable."),
            ("rev", "As a reviewer you also see how many open FDRs have documents that have not been analysed, and the number of open exceptions."),
            ("inst", "Next step: open FDRs to create, edit or submit your records."),
            ("rev", "Next step: open the Review queue to work through submitted FDRs."),
        ],
        "hi": [
            ("*", "यह ओवरव्यू उन रिकॉर्ड का सारांश है जिन्हें आप देख सकते हैं। कार्ड स्थिति के अनुसार संख्या, सक्रिय FDRs की मूल राशि (जैसी जमा की गई), 30 दिनों में परिपक्व होने वाली और परिपक्वता पार कर चुकी FDRs दिखाते हैं; हर आँकड़ा सर्वर वर्तमान रिकॉर्ड से निकालता है।"),
            ("*", "Needs attention सबसे ज़रूरी alerts दिखाता है, Recent FDRs और Recent activity नवीनतम रिकॉर्ड और स्थिति-परिवर्तन दिखाते हैं, और System status बताता है कि API और डेटाबेस पहुँच में हैं या नहीं।"),
            ("rev", "समीक्षक के रूप में आपको यह भी दिखता है कि कितनी खुली FDRs के दस्तावेज़ों का विश्लेषण नहीं हुआ है और कितनी exceptions खुली हैं।"),
            ("inst", "अगला कदम: अपने रिकॉर्ड बनाने, संपादित करने या जमा करने के लिए FDRs खोलें।"),
            ("rev", "अगला कदम: जमा की गई FDRs पर काम करने के लिए Review queue खोलें।"),
        ],
        "mr": [
            ("*", "हे ओव्हरव्ह्यू तुम्ही पाहू शकता अशा नोंदींचा सारांश देते. कार्ड्स स्थितीनुसार संख्या, सक्रिय FDRs ची मूळ रक्कम (सादर केल्याप्रमाणे), 30 दिवसांत परिपक्व होणाऱ्या आणि परिपक्वता उलटून गेलेल्या FDRs दाखवतात; प्रत्येक आकडा सर्व्हर सध्याच्या नोंदींवरून काढतो."),
            ("*", "Needs attention सर्वात तातडीचे alerts दाखवते, Recent FDRs आणि Recent activity ताज्या नोंदी आणि स्थितीतील बदल दाखवतात, आणि System status API व डेटाबेस उपलब्ध आहेत का ते सांगते."),
            ("rev", "समीक्षक म्हणून तुम्हाला हेही दिसते की किती खुल्या FDRs च्या कागदपत्रांचे विश्लेषण झालेले नाही आणि किती exceptions खुल्या आहेत."),
            ("inst", "पुढचे पाऊल: तुमच्या नोंदी तयार करण्यासाठी, संपादित करण्यासाठी किंवा सादर करण्यासाठी FDRs उघडा."),
            ("rev", "पुढचे पाऊल: सादर केलेल्या FDRs वर काम करण्यासाठी Review queue उघडा."),
        ],
    },
    "fdrs": {
        "en": [
            ("*", "The FDR registry lists every FDR you may see. Search matches the start of the FDR number; the Status, Bank and (for AICTE roles) Institution filters narrow the list; click a column heading to sort by it, and click it again to reverse the order. Sorting and paging are done by the server."),
            ("*", "Choose View details on a row to open the record with its status history. Status badges are written out in words, and amounts are as submitted."),
            ("inst", "You can start a new record with Create FDR, and Edit draft or Make corrections appears on your own records that are still editable."),
            ("rev", "You cannot create or edit FDRs here: only institution users can. Reviewers act on submitted records from the Review queue."),
            ("admin", "You can browse the registry but not edit FDRs; administrators manage user accounts."),
            ("aud", "You have read-only access to the registry."),
        ],
        "hi": [
            ("*", "FDR रजिस्ट्री उन सभी FDRs की सूची है जिन्हें आप देख सकते हैं। खोज FDR नंबर के आरंभ से मेल खाती है; Status, Bank और (AICTE भूमिकाओं के लिए) Institution फ़िल्टर सूची को सीमित करते हैं; किसी कॉलम शीर्षक पर क्लिक करने से उसके अनुसार क्रम बदलता है, दोबारा क्लिक करने पर उल्टा क्रम होता है। क्रमबद्धता और पृष्ठांकन सर्वर करता है।"),
            ("*", "किसी पंक्ति में View details चुनकर रिकॉर्ड को उसके स्थिति-इतिहास के साथ खोलें। स्थिति के बैज शब्दों में लिखे होते हैं और राशियाँ जैसी जमा की गईं वैसी हैं।"),
            ("inst", "आप Create FDR से नया रिकॉर्ड शुरू कर सकते हैं, और अपने जो रिकॉर्ड अभी संपादन योग्य हैं उन पर Edit draft या Make corrections दिखता है।"),
            ("rev", "आप यहाँ FDRs नहीं बना या संपादित कर सकते: यह केवल संस्थान उपयोगकर्ता कर सकते हैं। समीक्षक जमा किए गए रिकॉर्ड पर Review queue से कार्य करते हैं।"),
            ("admin", "आप रजिस्ट्री देख सकते हैं पर FDRs संपादित नहीं कर सकते; प्रशासक उपयोगकर्ता खाते प्रबंधित करते हैं।"),
            ("aud", "रजिस्ट्री में आपकी पहुँच केवल पढ़ने की है।"),
        ],
        "mr": [
            ("*", "FDR नोंदणी तुम्ही पाहू शकता अशा सर्व FDRs ची यादी आहे. शोध FDR क्रमांकाच्या सुरुवातीशी जुळतो; Status, Bank आणि (AICTE भूमिकांसाठी) Institution फिल्टर्स यादी मर्यादित करतात; स्तंभ शीर्षकावर क्लिक केल्यास त्यानुसार क्रम लागतो, पुन्हा क्लिक केल्यास उलटा क्रम होतो. क्रम लावणे आणि पृष्ठे सर्व्हर करतो."),
            ("*", "एखाद्या ओळीत View details निवडून नोंद तिच्या स्थिती-इतिहासासह उघडा. स्थितीचे बॅज शब्दांत लिहिलेले असतात आणि रकमा सादर केल्याप्रमाणे आहेत."),
            ("inst", "तुम्ही Create FDR ने नवीन नोंद सुरू करू शकता, आणि तुमच्या ज्या नोंदी अजून संपादित करता येतात त्यांवर Edit draft किंवा Make corrections दिसते."),
            ("rev", "तुम्ही येथे FDRs तयार किंवा संपादित करू शकत नाही: ते फक्त संस्था वापरकर्ते करू शकतात. समीक्षक सादर केलेल्या नोंदींवर Review queue मधून काम करतात."),
            ("admin", "तुम्ही नोंदणी पाहू शकता पण FDRs संपादित करू शकत नाही; प्रशासक वापरकर्ता खाती व्यवस्थापित करतात."),
            ("aud", "नोंदणीमध्ये तुमचा प्रवेश फक्त वाचनासाठी आहे."),
        ],
    },
    "fdr_new": {
        "en": [
            ("*", "This form creates a new FDR as a draft that only your institution can see. Choose the bank, then enter the FDR number, principal amount, issue date and maturity date; branch, depositor, interest rate and purpose are optional until you submit."),
            ("*", "The server checks every field again when you save, and refuses a number already used by another live FDR at the same bank. Saving does not submit the record."),
        ],
        "hi": [
            ("*", "यह फ़ॉर्म नई FDR को ड्राफ्ट के रूप में बनाता है जिसे केवल आपका संस्थान देख सकता है। बैंक चुनें, फिर FDR नंबर, मूल राशि, जारी करने की तिथि और परिपक्वता तिथि भरें; शाखा, जमाकर्ता, ब्याज दर और उद्देश्य जमा करने तक वैकल्पिक हैं।"),
            ("*", "सहेजते समय सर्वर हर फ़ील्ड की दोबारा जाँच करता है, और उसी बैंक की किसी अन्य सक्रिय FDR द्वारा उपयोग किए गए नंबर को अस्वीकार करता है। सहेजने से रिकॉर्ड जमा नहीं होता।"),
        ],
        "mr": [
            ("*", "हा फॉर्म नवीन FDR ड्राफ्ट म्हणून तयार करतो, जी फक्त तुमची संस्था पाहू शकते. बँक निवडा, मग FDR क्रमांक, मूळ रक्कम, जारी तारीख आणि परिपक्वता तारीख भरा; शाखा, ठेवीदार, व्याजदर आणि उद्देश सादर करेपर्यंत ऐच्छिक आहेत."),
            ("*", "जतन करताना सर्व्हर प्रत्येक फील्ड पुन्हा तपासतो, आणि त्याच बँकेतील दुसऱ्या सक्रिय FDR ने वापरलेला क्रमांक नाकारतो. जतन केल्याने नोंद सादर होत नाही."),
        ],
    },
    "fdr_edit": {
        "en": [
            ("*", "This page edits a draft, or a record returned for correction. Save keeps your changes; Submit for review sends the record to AICTE and, once submitted, it cannot be edited until a reviewer returns it."),
            ("*", "The documents panel lets you add or remove supporting files while the record is editable. A reviewer's comment, if any, is in the status history."),
        ],
        "hi": [
            ("*", "यह पेज ड्राफ्ट या सुधार के लिए लौटाए गए रिकॉर्ड को संपादित करता है। Save आपके बदलाव रखता है; Submit for review रिकॉर्ड को AICTE को भेजता है और जमा होने के बाद जब तक समीक्षक इसे न लौटाए, इसे संपादित नहीं किया जा सकता।"),
            ("*", "दस्तावेज़ पैनल से आप रिकॉर्ड के संपादन योग्य रहते सहायक फ़ाइलें जोड़ या हटा सकते हैं। समीक्षक की टिप्पणी, यदि कोई हो, स्थिति-इतिहास में है।"),
        ],
        "mr": [
            ("*", "हे पान ड्राफ्ट किंवा दुरुस्तीसाठी परत पाठवलेली नोंद संपादित करते. Save तुमचे बदल ठेवते; Submit for review नोंद AICTE ला पाठवते आणि सादर केल्यानंतर समीक्षक ती परत पाठवेपर्यंत संपादित करता येत नाही."),
            ("*", "कागदपत्र पॅनेलमधून तुम्ही नोंद संपादित करता येत असताना सहाय्यक फाइल्स जोडू किंवा काढू शकता. समीक्षकाची टिप्पणी, असल्यास, स्थिती-इतिहासात आहे."),
        ],
    },
    "fdr_detail": {
        "en": [
            ("*", "This page shows one FDR exactly as it was entered, with its full status history (oldest first, people identified by role). It does not verify anything with a bank."),
            ("inst", "You can edit only while the record is a draft or has been returned for correction. Documents and Release / refund open from the buttons below the details."),
            ("rev", "Use Open for review or Verification below the details to work on the record; you cannot edit it. Only an approver can accept or reject."),
            ("admin", "You can read this record but not edit or decide it."),
            ("aud", "You have read-only access to this record."),
        ],
        "hi": [
            ("*", "यह पेज एक FDR को ठीक वैसे दिखाता है जैसे वह भरी गई थी, उसके पूरे स्थिति-इतिहास के साथ (सबसे पुराना पहले, लोगों की पहचान भूमिका से)। यह बैंक से कुछ भी सत्यापित नहीं करता।"),
            ("inst", "आप केवल तभी संपादित कर सकते हैं जब रिकॉर्ड ड्राफ्ट हो या सुधार के लिए लौटाया गया हो। Documents और Release / refund विवरण के नीचे के बटनों से खुलते हैं।"),
            ("rev", "आप विवरण के नीचे के बटनों से रिकॉर्ड को समीक्षा या सत्यापन के लिए खोल सकते हैं; इसे संपादित नहीं कर सकते। केवल अनुमोदक स्वीकार या अस्वीकार कर सकता है।"),
            ("admin", "आप यह रिकॉर्ड पढ़ सकते हैं पर उसे संपादित या उस पर निर्णय नहीं ले सकते।"),
            ("aud", "इस रिकॉर्ड में आपकी पहुँच केवल पढ़ने की है।"),
        ],
        "mr": [
            ("*", "हे पान एक FDR जशी भरली होती तशीच दाखवते, तिच्या संपूर्ण स्थिती-इतिहासासह (सर्वात जुने आधी, व्यक्तींची ओळख भूमिकेने). ते बँकेकडून काहीही सत्यापित करत नाही."),
            ("inst", "नोंद ड्राफ्ट असताना किंवा दुरुस्तीसाठी परत पाठवलेली असतानाच तुम्ही संपादित करू शकता. Documents आणि Release / refund तपशीलाखालील बटणांतून उघडतात."),
            ("rev", "तुम्ही तपशीलाखालील बटणांतून नोंद समीक्षा किंवा सत्यापनासाठी उघडू शकता; ती संपादित करू शकत नाही. फक्त मंजुरी देणारा स्वीकारू किंवा नाकारू शकतो."),
            ("admin", "तुम्ही ही नोंद वाचू शकता पण संपादित करू किंवा त्यावर निर्णय घेऊ शकत नाही."),
            ("aud", "या नोंदीमध्ये तुमचा प्रवेश फक्त वाचनासाठी आहे."),
        ],
    },
    "review": {
        "en": [
            ("*", "The Review queue lists FDRs waiting for a decision (status Submitted), oldest submission first, each with a summary of its stored document-check results. Open one to review it."),
            ("rev", "Reviewers can request a correction with a comment; only approvers can accept or reject. The document summary is a reading aid and never a bank verification."),
        ],
        "hi": [
            ("*", "Review queue उन FDRs की सूची है जो निर्णय की प्रतीक्षा में हैं (स्थिति Submitted), सबसे पुरानी प्रस्तुति पहले, हर एक के साथ उसके संग्रहीत दस्तावेज़-जाँच परिणामों का सारांश। समीक्षा के लिए किसी को खोलें।"),
            ("rev", "समीक्षक टिप्पणी के साथ सुधार का अनुरोध कर सकते हैं; केवल अनुमोदक स्वीकार या अस्वीकार कर सकते हैं। दस्तावेज़ सारांश पढ़ने में सहायता है, बैंक सत्यापन नहीं।"),
        ],
        "mr": [
            ("*", "Review queue निर्णयाच्या प्रतीक्षेतील FDRs ची यादी आहे (स्थिती Submitted), सर्वात जुने सादरीकरण आधी, प्रत्येकासोबत त्याच्या साठवलेल्या कागदपत्र-तपासणी निकालांचा सारांश. समीक्षेसाठी एखादी उघडा."),
            ("rev", "समीक्षक टिप्पणीसह दुरुस्तीची विनंती करू शकतात; फक्त मंजुरी देणारे स्वीकारू किंवा नाकारू शकतात. कागदपत्र सारांश वाचनास मदत आहे, बँक सत्यापन नाही."),
        ],
    },
    "review_detail": {
        "en": [
            ("*", "This is the review page for one submitted FDR: its details, the status history and the stored document-check results."),
            ("rev", "A reviewer can request a correction and must give a comment. An approver can also accept or reject. Every decision is recorded in the audit trail under the decider's name, and a decision cannot be repeated once taken."),
        ],
        "hi": [
            ("*", "यह जमा की गई एक FDR का समीक्षा पेज है: उसका विवरण, स्थिति-इतिहास और संग्रहीत दस्तावेज़-जाँच परिणाम।"),
            ("rev", "समीक्षक सुधार का अनुरोध कर सकता है और टिप्पणी देना आवश्यक है। अनुमोदक स्वीकार या अस्वीकार भी कर सकता है। हर निर्णय ऑडिट ट्रेल में निर्णय लेने वाले के नाम से दर्ज होता है, और एक बार लिया गया निर्णय दोहराया नहीं जा सकता।"),
        ],
        "mr": [
            ("*", "हे सादर केलेल्या एका FDR चे समीक्षा पान आहे: तिचा तपशील, स्थिती-इतिहास आणि साठवलेले कागदपत्र-तपासणी निकाल."),
            ("rev", "समीक्षक दुरुस्तीची विनंती करू शकतो आणि टिप्पणी देणे आवश्यक आहे. मंजुरी देणारा स्वीकारू किंवा नाकारूही शकतो. प्रत्येक निर्णय ऑडिट ट्रेलमध्ये निर्णय घेणाऱ्याच्या नावाने नोंदवला जातो, आणि एकदा घेतलेला निर्णय पुन्हा घेता येत नाही."),
        ],
    },
    "documents": {
        "en": [
            ("*", "Documents lists FDRs; choose one to see, download or (for institution users) upload its supporting files. Accepted files are PDF, JPEG and PNG up to 10 MB, at most 20 per FDR."),
            ("inst", "You can add or delete documents only while the FDR is a draft or returned for correction. Document analysis results are visible to AICTE reviewers only."),
            ("rev", "You can read and download documents but not add or delete them."),
        ],
        "hi": [
            ("*", "Documents FDRs की सूची दिखाता है; किसी को चुनकर उसकी सहायक फ़ाइलें देखें, डाउनलोड करें या (संस्थान उपयोगकर्ता) अपलोड करें। स्वीकार्य फ़ाइलें PDF, JPEG और PNG हैं, 10 MB तक, प्रति FDR अधिकतम 20।"),
            ("inst", "आप दस्तावेज़ केवल तभी जोड़ या हटा सकते हैं जब FDR ड्राफ्ट हो या सुधार के लिए लौटाई गई हो। दस्तावेज़ विश्लेषण के परिणाम केवल AICTE समीक्षकों को दिखते हैं।"),
            ("rev", "आप दस्तावेज़ पढ़ और डाउनलोड कर सकते हैं पर जोड़ या हटा नहीं सकते।"),
        ],
        "mr": [
            ("*", "Documents FDRs ची यादी दाखवते; एक निवडून तिच्या सहाय्यक फाइल्स पहा, डाउनलोड करा किंवा (संस्था वापरकर्ते) अपलोड करा. स्वीकारल्या जाणाऱ्या फाइल्स PDF, JPEG आणि PNG आहेत, 10 MB पर्यंत, प्रति FDR जास्तीत जास्त 20."),
            ("inst", "तुम्ही कागदपत्रे फक्त FDR ड्राफ्ट असताना किंवा दुरुस्तीसाठी परत पाठवलेली असताना जोडू किंवा काढू शकता. कागदपत्र विश्लेषण निकाल फक्त AICTE समीक्षकांना दिसतात."),
            ("rev", "तुम्ही कागदपत्रे वाचू आणि डाउनलोड करू शकता पण जोडू किंवा काढू शकत नाही."),
        ],
    },
    "verification": {
        "en": [
            ("*", "Verification shows, for each FDR, the result of reading its documents and comparing the values with the submitted record: MATCH, MISMATCH, NOT_FOUND, AMBIGUOUS or NOT_COMPARABLE. Only a MATCH is agreement; the rest need a person."),
            ("rev", "You can start or retry a check. Results are a reading aid and are not a bank verification."),
        ],
        "hi": [
            ("*", "Verification हर FDR के लिए उसके दस्तावेज़ पढ़ने और मानों की जमा किए गए रिकॉर्ड से तुलना का परिणाम दिखाता है: MATCH, MISMATCH, NOT_FOUND, AMBIGUOUS या NOT_COMPARABLE। केवल MATCH सहमति है; बाकी के लिए किसी व्यक्ति को देखना ज़रूरी है।"),
            ("rev", "आप जाँच शुरू या दोबारा कर सकते हैं। परिणाम पढ़ने में सहायता हैं, बैंक सत्यापन नहीं।"),
        ],
        "mr": [
            ("*", "Verification प्रत्येक FDR साठी तिची कागदपत्रे वाचून मूल्यांची सादर केलेल्या नोंदीशी केलेल्या तुलनेचा निकाल दाखवते: MATCH, MISMATCH, NOT_FOUND, AMBIGUOUS किंवा NOT_COMPARABLE. फक्त MATCH म्हणजे सहमती; बाकीच्यांसाठी व्यक्तीने पाहणे आवश्यक आहे."),
            ("rev", "तुम्ही तपासणी सुरू किंवा पुन्हा करू शकता. निकाल वाचनास मदत आहेत, बँक सत्यापन नाही."),
        ],
    },
    "alerts": {
        "en": [
            ("*", "Alerts are calculated from current records every time you open the page: upcoming maturities, FDRs past maturity without an approved release, FDRs awaiting review, corrections requested, release requests waiting for a decision and failed document processing. Each links to the FDR."),
            ("*", "They are not stored or sent to anyone, and an alert disappears when its cause is fixed. There is nothing to acknowledge or dismiss."),
        ],
        "hi": [
            ("*", "Alerts हर बार पेज खोलने पर वर्तमान रिकॉर्ड से निकाले जाते हैं: आने वाली परिपक्वताएँ, स्वीकृत रिलीज़ के बिना परिपक्वता पार कर चुकी FDRs, समीक्षा की प्रतीक्षा में FDRs, मांगे गए सुधार, निर्णय की प्रतीक्षा में रिलीज़ अनुरोध और विफल दस्तावेज़ प्रसंस्करण। हर एक FDR से जुड़ा है।"),
            ("*", "इन्हें सहेजा या किसी को भेजा नहीं जाता, और कारण ठीक होते ही alert चला जाता है। स्वीकार करने या हटाने के लिए कुछ नहीं है।"),
        ],
        "mr": [
            ("*", "Alerts तुम्ही पान उघडता तेव्हा प्रत्येक वेळी सध्याच्या नोंदींवरून काढले जातात: येणाऱ्या परिपक्वता, मंजूर रिलीजशिवाय परिपक्वता उलटून गेलेल्या FDRs, समीक्षेच्या प्रतीक्षेतील FDRs, मागितलेल्या दुरुस्त्या, निर्णयाच्या प्रतीक्षेतील रिलीज विनंत्या आणि अयशस्वी कागदपत्र प्रक्रिया. प्रत्येक FDR शी जोडलेले आहे."),
            ("*", "ते जतन केले जात नाहीत किंवा कोणाला पाठवले जात नाहीत, आणि कारण दुरुस्त होताच alert नाहीसे होते. मान्य करण्यासाठी किंवा काढण्यासाठी काही नाही."),
        ],
    },
    "exceptions": {
        "en": [
            ("*", "Exceptions are problems found in the records: a document that differs from the submitted record, unclear or failed document reading, a missing document, an incomplete record, or an FDR past maturity with no approved release. The kind counts are at the top and the list is most serious first."),
            ("*", "They are derived on each request, so an exception goes away when its cause is fixed. A mismatch is not a finding of fraud; it means a person should look."),
        ],
        "hi": [
            ("*", "Exceptions रिकॉर्ड में पाई गई समस्याएँ हैं: जमा किए गए रिकॉर्ड से भिन्न दस्तावेज़, अस्पष्ट या विफल दस्तावेज़ पठन, अनुपस्थित दस्तावेज़, अधूरा रिकॉर्ड, या परिपक्वता पार कर चुकी FDR जिसकी स्वीकृत रिलीज़ नहीं है। ऊपर प्रकार-वार संख्या है और सूची में सबसे गंभीर पहले हैं।"),
            ("*", "ये हर अनुरोध पर निकाली जाती हैं, इसलिए कारण ठीक होते ही exception चली जाती है। mismatch धोखाधड़ी का निष्कर्ष नहीं है; इसका अर्थ है कि किसी व्यक्ति को देखना चाहिए।"),
        ],
        "mr": [
            ("*", "Exceptions म्हणजे नोंदींमध्ये आढळलेल्या समस्या: सादर केलेल्या नोंदीपेक्षा वेगळे कागदपत्र, अस्पष्ट किंवा अयशस्वी कागदपत्र वाचन, गहाळ कागदपत्र, अपूर्ण नोंद, किंवा परिपक्वता उलटून गेलेली आणि मंजूर रिलीज नसलेली FDR. वर प्रकारानुसार संख्या आहेत आणि यादीत सर्वात गंभीर आधी आहेत."),
            ("*", "ते प्रत्येक विनंतीवर काढले जातात, त्यामुळे कारण दुरुस्त होताच exception नाहीशी होते. mismatch हा फसवणुकीचा निष्कर्ष नाही; याचा अर्थ व्यक्तीने पाहावे."),
        ],
    },
    "release": {
        "en": [
            ("*", "Release / Refund is a request-and-approval workflow, not a payment. An FDR is eligible when it is Accepted and has reached its maturity date. This system records decisions and never transfers funds."),
            ("inst", "You can request release for an eligible FDR of your institution, and ask again if a request was rejected."),
            ("apr", "As an approver you approve or reject release requests; a rejection needs a reason."),
            ("rev", "As a reviewer you can read release requests; only an approver decides them."),
        ],
        "hi": [
            ("*", "Release / Refund अनुरोध और अनुमोदन की प्रक्रिया है, भुगतान नहीं। FDR तब पात्र है जब वह Accepted हो और उसकी परिपक्वता तिथि आ चुकी हो। यह प्रणाली निर्णय दर्ज करती है और कभी धन हस्तांतरित नहीं करती।"),
            ("inst", "आप अपने संस्थान की पात्र FDR के लिए रिलीज़ का अनुरोध कर सकते हैं, और अनुरोध अस्वीकृत होने पर फिर से कर सकते हैं।"),
            ("apr", "अनुमोदक के रूप में आप रिलीज़ अनुरोध स्वीकृत या अस्वीकृत करते हैं; अस्वीकृति के लिए कारण देना होता है।"),
            ("rev", "समीक्षक के रूप में आप रिलीज़ अनुरोध पढ़ सकते हैं; उन पर निर्णय केवल अनुमोदक लेता है।"),
        ],
        "mr": [
            ("*", "Release / Refund ही विनंती आणि मंजुरीची प्रक्रिया आहे, पैसे देणे नव्हे. FDR तेव्हा पात्र असते जेव्हा ती Accepted असते आणि तिची परिपक्वता तारीख आलेली असते. ही प्रणाली निर्णय नोंदवते आणि कधीही पैसे हस्तांतरित करत नाही."),
            ("inst", "तुम्ही तुमच्या संस्थेच्या पात्र FDR साठी रिलीजची विनंती करू शकता, आणि विनंती नाकारली गेल्यास पुन्हा करू शकता."),
            ("apr", "मंजुरी देणारा म्हणून तुम्ही रिलीज विनंत्या मंजूर किंवा नामंजूर करता; नामंजुरीसाठी कारण द्यावे लागते."),
            ("rev", "समीक्षक म्हणून तुम्ही रिलीज विनंत्या वाचू शकता; त्यांवर निर्णय फक्त मंजुरी देणारा घेतो."),
        ],
    },
    "reports": {
        "en": [
            ("*", "Reports summarise everything in your scope (not just one page of a list): FDRs by status and institution, the principal of live FDRs as submitted, upcoming maturities, release requests, document verification and exceptions. The small bars are drawn from the same counts as the tables."),
            ("*", "The tables and totals always cover your whole scope. The PDF reports (FDR summary, institution-wise summary and maturity report for everyone; pending review and exceptions for reviewers and approvers; the audit trail for administrators and auditors) can be narrowed by period or institution where an option is offered and print their scope and filters; they are not digitally signed and show English text only. The CSV downloads contain exactly the records you may see."),
        ],
        "hi": [
            ("*", "Reports आपके दायरे की हर चीज़ का सारांश देती हैं (सूची के केवल एक पृष्ठ का नहीं): स्थिति और संस्थान के अनुसार FDRs, सक्रिय FDRs की मूल राशि जैसी जमा की गई, आने वाली परिपक्वताएँ, रिलीज़ अनुरोध, दस्तावेज़ सत्यापन और exceptions। छोटी पट्टियाँ तालिकाओं की संख्याओं से ही बनी हैं।"),
            ("*", "तालिकाएँ और कुल हमेशा आपके पूरे दायरे को कवर करते हैं। PDF reports (सभी के लिए FDR summary, institution-wise summary और maturity report; समीक्षकों और अनुमोदकों के लिए pending review और exceptions; प्रशासकों और ऑडिटरों के लिए audit trail) जहाँ विकल्प दिया गया है वहाँ अवधि या संस्थान से सीमित की जा सकती हैं और अपना दायरा तथा फ़िल्टर छापती हैं; वे डिजिटल हस्ताक्षरित नहीं हैं और केवल अंग्रेज़ी पाठ दिखाती हैं। CSV डाउनलोड में ठीक वही रिकॉर्ड होते हैं जिन्हें आप देख सकते हैं।"),
        ],
        "mr": [
            ("*", "Reports तुमच्या कार्यक्षेत्रातील सर्वकाही सारांशित करतात (यादीच्या फक्त एका पृष्ठाचे नव्हे): स्थिती आणि संस्थेनुसार FDRs, सक्रिय FDRs ची मूळ रक्कम सादर केल्याप्रमाणे, येणाऱ्या परिपक्वता, रिलीज विनंत्या, कागदपत्र सत्यापन आणि exceptions. छोट्या पट्ट्या तक्त्यांतील संख्यांवरूनच काढलेल्या आहेत."),
            ("*", "तक्ते आणि एकूण नेहमी तुमचे संपूर्ण कार्यक्षेत्र व्यापतात. PDF reports (सर्वांसाठी FDR summary, institution-wise summary आणि maturity report; समीक्षक आणि मंजुरी देणाऱ्यांसाठी pending review आणि exceptions; प्रशासक आणि ऑडिटरसाठी audit trail) पर्याय दिला असेल तेथे कालावधी किंवा संस्थेनुसार मर्यादित करता येतात आणि त्यांचे कार्यक्षेत्र व फिल्टर छापतात; त्या डिजिटल स्वाक्षरीत नाहीत आणि फक्त इंग्रजी मजकूर दाखवतात. CSV डाउनलोडमध्ये तुम्ही पाहू शकता तेच नेमके रेकॉर्ड असतात."),
        ],
    },
    "calendar": {
        "en": [
            ("*", "The Calendar shows FDR maturity dates by month or week. Markers say Past maturity, Due soon (within 30 days) or Upcoming, and each is also written in words. Choose an event to open the FDR."),
            ("*", "Only a maturity date exists in the data: the system has no separate renewal deadline, and a past date is shown for attention, not as proof that anything is wrong. Use the filters to narrow by status (and by institution for AICTE roles)."),
        ],
        "hi": [
            ("*", "Calendar FDR की परिपक्वता तिथियाँ माह या सप्ताह के अनुसार दिखाता है। चिह्न Past maturity, Due soon (30 दिनों के भीतर) या Upcoming बताते हैं, और हर एक शब्दों में भी लिखा है। FDR खोलने के लिए कोई घटना चुनें।"),
            ("*", "डेटा में केवल परिपक्वता तिथि है: प्रणाली में अलग से कोई नवीनीकरण की अंतिम तिथि नहीं है, और बीती तिथि ध्यान के लिए दिखाई जाती है, यह प्रमाण नहीं कि कुछ गलत है। स्थिति (और AICTE भूमिकाओं के लिए संस्थान) से सीमित करने के लिए फ़िल्टर का उपयोग करें।"),
        ],
        "mr": [
            ("*", "Calendar FDR च्या परिपक्वता तारखा महिना किंवा आठवड्यानुसार दाखवते. खुणा Past maturity, Due soon (30 दिवसांत) किंवा Upcoming सांगतात, आणि प्रत्येक शब्दांतही लिहिलेली आहे. FDR उघडण्यासाठी एखादी घटना निवडा."),
            ("*", "डेटामध्ये फक्त परिपक्वता तारीख आहे: प्रणालीत स्वतंत्र नूतनीकरण अंतिम तारीख नाही, आणि उलटून गेलेली तारीख लक्ष वेधण्यासाठी दाखवली जाते, काही चुकले आहे याचा पुरावा म्हणून नव्हे. स्थितीनुसार (आणि AICTE भूमिकांसाठी संस्थेनुसार) मर्यादित करण्यासाठी फिल्टर वापरा."),
        ],
    },
    "workload": {
        "en": [
            ("*", "Reviewer workload shows the review queue: how many FDRs are waiting, how long the oldest has waited, how many reviews were completed in the chosen period and how long they took, from the real submission and decision times."),
            ("*", "No review deadline is built into the system, so unless one is configured only elapsed waiting time is shown. Assignment is not tracked, so there are no assigned or unassigned counts."),
            ("admin", "As an administrator you also see decisions by named person; reviewers and approvers see their own count and totals by role."),
        ],
        "hi": [
            ("*", "Reviewer workload समीक्षा कतार दिखाता है: कितनी FDRs प्रतीक्षा में हैं, सबसे पुरानी कितने समय से प्रतीक्षा में है, चुनी गई अवधि में कितनी समीक्षाएँ पूरी हुईं और कितना समय लगा, वास्तविक जमा और निर्णय के समय से।"),
            ("*", "प्रणाली में कोई समीक्षा समय-सीमा अंतर्निहित नहीं है, इसलिए जब तक कोई कॉन्फ़िगर न की जाए केवल बीता हुआ प्रतीक्षा समय दिखता है। असाइनमेंट ट्रैक नहीं किया जाता, इसलिए असाइन्ड या अनअसाइन्ड संख्या नहीं है।"),
            ("admin", "प्रशासक के रूप में आप नाम सहित व्यक्ति-वार निर्णय भी देखते हैं; समीक्षक और अनुमोदक अपनी संख्या और भूमिका-वार कुल देखते हैं।"),
        ],
        "mr": [
            ("*", "Reviewer workload समीक्षा रांग दाखवते: किती FDRs प्रतीक्षेत आहेत, सर्वात जुनी किती वेळ थांबली आहे, निवडलेल्या कालावधीत किती समीक्षा पूर्ण झाल्या आणि किती वेळ लागला, प्रत्यक्ष सादरीकरण आणि निर्णयाच्या वेळांवरून."),
            ("*", "प्रणालीत कोणतीही समीक्षा मुदत अंगभूत नाही, त्यामुळे ती कॉन्फिगर केल्याशिवाय फक्त उलटलेला प्रतीक्षा वेळ दिसतो. असाइनमेंट ट्रॅक केले जात नाही, म्हणून असाइन्ड किंवा अनअसाइन्ड संख्या नाहीत."),
            ("admin", "प्रशासक म्हणून तुम्हाला नावासह व्यक्तिनिहाय निर्णयही दिसतात; समीक्षक आणि मंजुरी देणारे स्वतःची संख्या आणि भूमिकेनुसार एकूण पाहतात."),
        ],
    },
    "admin": {
        "en": [
            ("*", "Administration manages who can use the portal: user accounts, institutions and banks. There is no self-registration; a new user must change the temporary password at first sign-in, and every change is recorded in the audit trail under your name."),
            ("*", "Administrators cannot create, edit or decide FDRs."),
        ],
        "hi": [
            ("*", "Administration यह प्रबंधित करता है कि पोर्टल कौन उपयोग कर सकता है: उपयोगकर्ता खाते, संस्थान और बैंक। स्व-पंजीकरण नहीं है; नए उपयोगकर्ता को पहली बार साइन-इन पर अस्थायी पासवर्ड बदलना होता है, और हर बदलाव ऑडिट ट्रेल में आपके नाम से दर्ज होता है।"),
            ("*", "प्रशासक FDRs बना, संपादित या उन पर निर्णय नहीं ले सकते।"),
        ],
        "mr": [
            ("*", "Administration पोर्टल कोण वापरू शकते हे व्यवस्थापित करते: वापरकर्ता खाती, संस्था आणि बँका. स्वतःची नोंदणी नाही; नवीन वापरकर्त्याला पहिल्या साइन-इनवर तात्पुरता पासवर्ड बदलावा लागतो, आणि प्रत्येक बदल ऑडिट ट्रेलमध्ये तुमच्या नावाने नोंदवला जातो."),
            ("*", "प्रशासक FDRs तयार, संपादित किंवा त्यांवर निर्णय घेऊ शकत नाहीत."),
        ],
    },
    "security": {
        "en": [
            ("*", "Security shows your own sign-in sessions from the server: when each started, when it was last used, the browser it used and a coarse location marker. Your current session is marked. You can end one session or all your other sessions, which stops them working immediately."),
            ("*", "Recent sign-in activity lists your own successful and failed sign-ins as recorded in the audit trail. Nothing is shown about anyone else's account unless you are an administrator."),
        ],
        "hi": [
            ("*", "Security सर्वर से आपके अपने साइन-इन सत्र दिखाता है: हर सत्र कब शुरू हुआ, आखिरी बार कब उपयोग हुआ, कौन सा ब्राउज़र था और मोटा स्थान-संकेत। आपका वर्तमान सत्र चिह्नित है। आप एक सत्र या अपने बाकी सभी सत्र समाप्त कर सकते हैं, जिससे वे तुरंत काम करना बंद कर देते हैं।"),
            ("*", "Recent sign-in activity ऑडिट ट्रेल में दर्ज आपके अपने सफल और असफल साइन-इन सूचीबद्ध करता है। प्रशासक न होने पर किसी और के खाते के बारे में कुछ नहीं दिखता।"),
        ],
        "mr": [
            ("*", "Security सर्व्हरवरून तुमची स्वतःची साइन-इन सत्रे दाखवते: प्रत्येक सत्र कधी सुरू झाले, शेवटचे कधी वापरले, कोणता ब्राउझर होता आणि ढोबळ स्थान-खूण. तुमचे सध्याचे सत्र चिन्हांकित आहे. तुम्ही एक सत्र किंवा तुमची इतर सर्व सत्रे संपवू शकता, ज्यामुळे ती लगेच काम करणे थांबवतात."),
            ("*", "Recent sign-in activity ऑडिट ट्रेलमध्ये नोंदलेले तुमचे स्वतःचे यशस्वी आणि अयशस्वी साइन-इन यादीत दाखवते. प्रशासक नसल्यास इतर कोणाच्या खात्याबद्दल काही दिसत नाही."),
        ],
    },
    "import": {
        "en": [
            ("*", "Bulk import creates many FDRs from a CSV file. Download the template, fill it in, upload it and review the preview: valid rows and rejected rows (with the reason for each) are shown before anything is saved. Confirm to create the valid rows."),
            ("*", "Every imported row becomes a draft that only your institution can see; a status column in the file is refused. Importing never submits, accepts or releases anything, and the same rules as the Create FDR form apply."),
        ],
        "hi": [
            ("*", "Bulk import एक CSV फ़ाइल से कई FDRs बनाता है। टेम्पलेट डाउनलोड करें, भरें, अपलोड करें और प्रीव्यू देखें: कुछ भी सहेजने से पहले मान्य पंक्तियाँ और अस्वीकृत पंक्तियाँ (हर एक के कारण सहित) दिखाई जाती हैं। मान्य पंक्तियाँ बनाने के लिए पुष्टि करें।"),
            ("*", "हर आयात की गई पंक्ति ड्राफ्ट बनती है जिसे केवल आपका संस्थान देख सकता है; फ़ाइल में status कॉलम अस्वीकार किया जाता है। आयात कभी कुछ जमा, स्वीकृत या रिलीज़ नहीं करता, और Create FDR फ़ॉर्म के वही नियम लागू होते हैं।"),
        ],
        "mr": [
            ("*", "Bulk import एका CSV फाइलमधून अनेक FDRs तयार करते. टेम्पलेट डाउनलोड करा, भरा, अपलोड करा आणि पूर्वावलोकन पहा: काहीही जतन होण्यापूर्वी वैध ओळी आणि नाकारलेल्या ओळी (प्रत्येकाच्या कारणासह) दाखवल्या जातात. वैध ओळी तयार करण्यासाठी खात्री करा."),
            ("*", "आयात केलेली प्रत्येक ओळ ड्राफ्ट बनते जी फक्त तुमची संस्था पाहू शकते; फाइलमधील status स्तंभ नाकारला जातो. आयात कधीही काही सादर, स्वीकृत किंवा रिलीज करत नाही, आणि Create FDR फॉर्मचेच नियम लागू होतात."),
        ],
    },
    "default": {
        "en": [("*", "This page is part of the FDR portal. Use the sidebar to move between sections; you only see what your role may use. Ask a question here about the portal or about records you can see.")],
        "hi": [("*", "यह पेज FDR पोर्टल का हिस्सा है। अनुभागों के बीच जाने के लिए साइडबार का उपयोग करें; आपको केवल वही दिखता है जो आपकी भूमिका उपयोग कर सकती है। पोर्टल या आपके देखने योग्य रिकॉर्ड के बारे में यहाँ प्रश्न पूछें।")],
        "mr": [("*", "हे पान FDR पोर्टलचा भाग आहे. विभागांदरम्यान जाण्यासाठी साइडबार वापरा; तुमच्या भूमिकेला जे वापरता येते तेवढेच तुम्हाला दिसते. पोर्टलबद्दल किंवा तुम्ही पाहू शकता त्या नोंदींबद्दल येथे प्रश्न विचारा.")],
    },
}
# A page without its own help falls back to its parent section.
PARENT = {"verification_detail": "verification", "documents_detail": "documents", "release_detail": "release"}

_AUDIENCE = {
    Role.INSTITUTION_USER.value: {"*", "inst"},
    Role.AICTE_REVIEWER.value: {"*", "rev"},
    Role.AICTE_APPROVER.value: {"*", "rev", "apr"},
    Role.ADMIN.value: {"*", "admin"},
    Role.AUDITOR.value: {"*", "aud"},
}


def page_help(page: str | None, role: str, language: str) -> str:
    """The help text for a page, for this role and language. Unknown pages get the generic help."""
    key = PARENT.get(page or "", page or "default")
    entries = PAGES.get(key) or PAGES["default"]
    sentences = entries.get(language) or entries["en"]
    audiences = _AUDIENCE.get(role, {"*"})
    return " ".join(text for who, text in sentences if who in audiences)


# --------------------------------------------------------------------------------------------------------------
# What a status means for the record being viewed, and the next workflow step for the viewer's role group.
# group: "inst" institution users, "rev" reviewers and approvers, "other" administrators and auditors.
STATUS_HELP: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "DRAFT": {"meaning": "This FDR is a draft: only your institution can see it and it can still be edited.",
                  "inst": "Next: attach documents, check the details, then use Submit for review."},
        "SUBMITTED": {"meaning": "This FDR has been submitted and is waiting in the AICTE review queue.",
                      "inst": "It cannot be edited while it waits; a reviewer may return it for correction.",
                      "rev": "Next: open it from the Review queue. Reviewers can request a correction; only an approver can accept or reject.",
                      "other": "It is waiting for a reviewer or approver."},
        "CORRECTION_REQUESTED": {"meaning": "A reviewer returned this FDR with a comment.",
                                 "inst": "Next: read the comment in the status history, make the corrections, then submit it again.",
                                 "rev": "It is waiting for the institution to correct and resubmit.",
                                 "other": "It is waiting for the institution to correct and resubmit."},
        "ACCEPTED": {"meaning": "This FDR was accepted after human review of the submitted information. It has not been verified with a bank.",
                     "inst": "Next: once it has matured you can request release / refund.",
                     "rev": "Release requests appear under Release / Refund; only an approver decides them.",
                     "other": "Release requests are decided by an approver."},
        "REJECTED": {"meaning": "This FDR was rejected by an approver; the reason is in the status history.",
                     "inst": "No further workflow step is available for a rejected FDR.", "rev": "No further workflow step is available.",
                     "other": "No further workflow step is available."},
        "WITHDRAWN": {"meaning": "This FDR was withdrawn and is no longer active.", "inst": "No further workflow step is available.",
                      "rev": "No further workflow step is available.", "other": "No further workflow step is available."},
    },
    "hi": {
        "DRAFT": {"meaning": "यह FDR ड्राफ्ट है: इसे केवल आपका संस्थान देख सकता है और इसे अभी संपादित किया जा सकता है।",
                  "inst": "अगला कदम: दस्तावेज़ संलग्न करें, विवरण जाँचें, फिर Submit for review का उपयोग करें।"},
        "SUBMITTED": {"meaning": "यह FDR जमा की जा चुकी है और AICTE समीक्षा कतार में प्रतीक्षा कर रही है।",
                      "inst": "प्रतीक्षा के दौरान इसे संपादित नहीं किया जा सकता; समीक्षक इसे सुधार के लिए लौटा सकता है।",
                      "rev": "अगला कदम: इसे Review queue से खोलें। समीक्षक सुधार का अनुरोध कर सकते हैं; केवल अनुमोदक स्वीकार या अस्वीकार कर सकता है।",
                      "other": "यह किसी समीक्षक या अनुमोदक की प्रतीक्षा में है।"},
        "CORRECTION_REQUESTED": {"meaning": "समीक्षक ने इस FDR को टिप्पणी के साथ लौटाया है।",
                                 "inst": "अगला कदम: स्थिति-इतिहास में टिप्पणी पढ़ें, सुधार करें, फिर इसे दोबारा जमा करें।",
                                 "rev": "यह संस्थान द्वारा सुधार और पुनः जमा किए जाने की प्रतीक्षा में है।",
                                 "other": "यह संस्थान द्वारा सुधार और पुनः जमा किए जाने की प्रतीक्षा में है।"},
        "ACCEPTED": {"meaning": "जमा की गई जानकारी की मानवीय समीक्षा के बाद यह FDR स्वीकृत हुई। इसे बैंक से सत्यापित नहीं किया गया है।",
                     "inst": "अगला कदम: परिपक्व होने के बाद आप रिलीज़ / refund का अनुरोध कर सकते हैं।",
                     "rev": "रिलीज़ अनुरोध Release / Refund में दिखते हैं; उन पर निर्णय केवल अनुमोदक लेता है।",
                     "other": "रिलीज़ अनुरोधों पर निर्णय अनुमोदक लेता है।"},
        "REJECTED": {"meaning": "इस FDR को अनुमोदक ने अस्वीकृत किया; कारण स्थिति-इतिहास में है।",
                     "inst": "अस्वीकृत FDR के लिए आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।", "rev": "आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।",
                     "other": "आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।"},
        "WITHDRAWN": {"meaning": "यह FDR वापस ली गई और अब सक्रिय नहीं है।", "inst": "आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।",
                      "rev": "आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।", "other": "आगे कोई प्रक्रिया-चरण उपलब्ध नहीं है।"},
    },
    "mr": {
        "DRAFT": {"meaning": "ही FDR ड्राफ्ट आहे: ती फक्त तुमची संस्था पाहू शकते आणि ती अजून संपादित करता येते.",
                  "inst": "पुढचे पाऊल: कागदपत्रे जोडा, तपशील तपासा, मग Submit for review वापरा."},
        "SUBMITTED": {"meaning": "ही FDR सादर केली गेली आहे आणि AICTE समीक्षा रांगेत प्रतीक्षा करत आहे.",
                      "inst": "प्रतीक्षेत असताना ती संपादित करता येत नाही; समीक्षक ती दुरुस्तीसाठी परत पाठवू शकतो.",
                      "rev": "पुढचे पाऊल: ती Review queue मधून उघडा. समीक्षक दुरुस्तीची विनंती करू शकतात; फक्त मंजुरी देणारा स्वीकारू किंवा नाकारू शकतो.",
                      "other": "ती समीक्षक किंवा मंजुरी देणाऱ्याच्या प्रतीक्षेत आहे."},
        "CORRECTION_REQUESTED": {"meaning": "समीक्षकाने ही FDR टिप्पणीसह परत पाठवली आहे.",
                                 "inst": "पुढचे पाऊल: स्थिती-इतिहासातील टिप्पणी वाचा, दुरुस्त्या करा, मग ती पुन्हा सादर करा.",
                                 "rev": "ती संस्थेने दुरुस्त करून पुन्हा सादर करण्याच्या प्रतीक्षेत आहे.",
                                 "other": "ती संस्थेने दुरुस्त करून पुन्हा सादर करण्याच्या प्रतीक्षेत आहे."},
        "ACCEPTED": {"meaning": "सादर केलेल्या माहितीच्या मानवी समीक्षेनंतर ही FDR स्वीकारली गेली. ती बँकेकडून सत्यापित केलेली नाही.",
                     "inst": "पुढचे पाऊल: परिपक्व झाल्यावर तुम्ही रिलीज / refund ची विनंती करू शकता.",
                     "rev": "रिलीज विनंत्या Release / Refund मध्ये दिसतात; त्यांवर निर्णय फक्त मंजुरी देणारा घेतो.",
                     "other": "रिलीज विनंत्यांवर निर्णय मंजुरी देणारा घेतो."},
        "REJECTED": {"meaning": "ही FDR मंजुरी देणाऱ्याने नाकारली; कारण स्थिती-इतिहासात आहे.",
                     "inst": "नाकारलेल्या FDR साठी पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही.", "rev": "पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही.",
                     "other": "पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही."},
        "WITHDRAWN": {"meaning": "ही FDR मागे घेतली गेली आणि आता सक्रिय नाही.", "inst": "पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही.",
                      "rev": "पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही.", "other": "पुढचा कोणताही प्रक्रिया-टप्पा उपलब्ध नाही."},
    },
}


def status_help(status: str, role: str, language: str) -> str:
    """Meaning of a status and the next step for this role. Status is the only record data used."""
    table = STATUS_HELP.get(language, STATUS_HELP["en"]).get(status)
    if table is None:
        return ""
    group = "inst" if role == Role.INSTITUTION_USER.value else "rev" if role in (Role.AICTE_REVIEWER.value, Role.AICTE_APPROVER.value) else "other"
    return " ".join(filter(None, [table["meaning"], table.get(group)]))
