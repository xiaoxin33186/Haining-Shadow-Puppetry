// 由旧素材标定数据换算的新骨骼 rest 数据（单肩轴 + 双髋轴）
// 骨架统一：4 个角色共用同一套锚点 / 骨长 / 骨段角度；素材不缩放，仅轴偏按各自素材适配
// 生成脚本不入库；本文件仅供 rig_lab 验证使用
window.REST_RIGS = {
  "xinwukong_material": {
    "name": "悟空",
    "folder": "xinwukong_material",
    "scale": 0.79,
    "pelvis": [
      0.0,
      0.0
    ],
    "neck": [
      4.51,
      -207.53
    ],
    "shoulder": [
      13.17,
      -193.1
    ],
    "shoulderEyes": {
      "right": [
        -8.75,
        -194.89
      ],
      "left": [
        35.09,
        -191.31
      ]
    },
    "hips": {
      "left": [
        23.17,
        7.56
      ],
      "right": [
        -23.17,
        -20.22
      ]
    },
    "phiTorso": -1.246,
    "parts": {
      "body": {
        "pivot": [
          54,
          3
        ],
        "size": [
          128,
          248
        ],
        "draw": 3,
        "mirror": false,
        "img": "../xinwukong_material/body.png",
        "pos": "neck",
        "axisOffset": 4.137
      },
      "head": {
        "pivot": [
          112,
          4
        ],
        "size": [
          154,
          176
        ],
        "draw": 2,
        "mirror": false,
        "img": "../xinwukong_material/head.png",
        "pos": "headEye",
        "eyeOffset": [
          52.62,
          -149.01
        ],
        "axisOffset": 6.377
      },
      "right_elbow": {
        "bone": "upperR",
        "pivot": [
          33,
          10
        ],
        "size": [
          86,
          157
        ],
        "draw": 4,
        "mirror": false,
        "img": "../xinwukong_material/right_elbow.png"
      },
      "right_wrist": {
        "bone": "foreR",
        "pivot": [
          53,
          11
        ],
        "size": [
          97,
          167
        ],
        "draw": 4,
        "mirror": false,
        "img": "../xinwukong_material/right_wrist.png"
      },
      "left_elbow": {
        "bone": "upperL",
        "pivot": [
          43,
          14
        ],
        "size": [
          81,
          159
        ],
        "draw": 1,
        "mirror": false,
        "img": "../xinwukong_material/left_elbow.png"
      },
      "left_wrist": {
        "bone": "foreL",
        "pivot": [
          52,
          14
        ],
        "size": [
          95,
          169
        ],
        "draw": 2,
        "mirror": false,
        "img": "../xinwukong_material/left_wrist.png"
      },
      "right_hip": {
        "bone": "thighR",
        "pivot": [
          61,
          7
        ],
        "size": [
          111,
          169
        ],
        "draw": 1,
        "mirror": false,
        "img": "../xinwukong_material/right_hip.png"
      },
      "right_knee": {
        "bone": "shinR",
        "pivot": [
          50,
          1
        ],
        "size": [
          102,
          170
        ],
        "draw": 3,
        "mirror": false,
        "img": "../xinwukong_material/right_knee.png"
      },
      "left_hip": {
        "bone": "thighL",
        "pivot": [
          46,
          5
        ],
        "size": [
          90,
          169
        ],
        "draw": 1,
        "mirror": false,
        "img": "../xinwukong_material/left_hip.png"
      },
      "left_knee": {
        "bone": "shinL",
        "pivot": [
          13,
          3
        ],
        "size": [
          96,
          161
        ],
        "draw": 3,
        "mirror": false,
        "img": "../xinwukong_material/left_knee.png"
      }
    },
    "bones": {
      "upperR": {
        "base": "shoulder",
        "len": 107.193,
        "phi": -9.02,
        "part": "right_elbow",
        "axisOffset": -1.183
      },
      "foreR": {
        "base": "elbowR",
        "len": 81.622,
        "phi": -14.263,
        "part": "right_wrist",
        "axisOffset": 333.25
      },
      "upperL": {
        "base": "shoulder",
        "len": 123.595,
        "phi": 45.645,
        "part": "left_elbow",
        "axisOffset": 363.558
      },
      "foreL": {
        "base": "elbowL",
        "len": 88.379,
        "phi": -17.295,
        "part": "left_wrist",
        "axisOffset": 306.268
      },
      "thighR": {
        "base": "hipR",
        "len": 129.977,
        "phi": -16.751,
        "part": "right_hip",
        "axisOffset": -6.099
      },
      "shinR": {
        "base": "kneeR",
        "len": 113.402,
        "phi": 8.965,
        "part": "right_knee",
        "axisOffset": 11.24
      },
      "thighL": {
        "base": "hipL",
        "len": 115.746,
        "phi": -14.105,
        "part": "left_hip",
        "axisOffset": 341.543
      },
      "shinL": {
        "base": "kneeL",
        "len": 104.993,
        "phi": 22.789,
        "part": "left_knee",
        "axisOffset": 378.695
      }
    },
    "limits": {
      "torso": [
        -25,
        25
      ],
      "head": [
        -10,
        10
      ],
      "shoulder": [
        -40,
        155
      ],
      "elbow": [
        0,
        150
      ],
      "hip": [
        -35,
        130
      ],
      "knee": [
        0,
        140
      ]
    },
    "hinge": {
      "elbowR": 1,
      "elbowL": 1,
      "kneeR": -1,
      "kneeL": -1
    },
    "bonesFit": {
      "upperR": {
        "base": "shoulder",
        "len": 107.193,
        "phi": -9.02,
        "part": "right_elbow",
        "axisOffset": -1.183
      },
      "foreR": {
        "base": "elbowR",
        "len": 81.622,
        "phi": -14.263,
        "part": "right_wrist",
        "axisOffset": 333.25
      },
      "upperL": {
        "base": "shoulder",
        "len": 123.595,
        "phi": 45.645,
        "part": "left_elbow",
        "axisOffset": 363.558
      },
      "foreL": {
        "base": "elbowL",
        "len": 88.379,
        "phi": -17.295,
        "part": "left_wrist",
        "axisOffset": 306.268
      },
      "thighR": {
        "base": "hipR",
        "len": 129.977,
        "phi": -16.751,
        "part": "right_hip",
        "axisOffset": -6.099
      },
      "shinR": {
        "base": "kneeR",
        "len": 113.402,
        "phi": 8.965,
        "part": "right_knee",
        "axisOffset": 11.24
      },
      "thighL": {
        "base": "hipL",
        "len": 115.746,
        "phi": -14.105,
        "part": "left_hip",
        "axisOffset": 341.543
      },
      "shinL": {
        "base": "kneeL",
        "len": 104.993,
        "phi": 22.789,
        "part": "left_knee",
        "axisOffset": 378.695
      }
    },
    "skeleton": "unified"
  },
  "new_sha_wujing_material": {
    "name": "新沙僧",
    "folder": "new_sha_wujing_material",
    "scale": 0.79,
    "pelvis": [
      0.0,
      0.0
    ],
    "neck": [
      75.4,
      -229.84
    ],
    "shoulder": [
      23.38,
      -146.76
    ],
    "shoulderEyes": {
      "right": [
        -30.86,
        -151.64
      ],
      "left": [
        77.62,
        -141.88
      ]
    },
    "hips": {
      "left": [
        51.34,
        -3.3
      ],
      "right": [
        -7.04,
        -0.7
      ]
    },
    "phiTorso": -18.162,
    "parts": {
      "body": {
        "pivot": [
          124,
          4
        ],
        "size": [
          160,
          250
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_sha_wujing_material/body.png",
        "pos": "neck",
        "axisOffset": -12.779
      },
      "head": {
        "pivot": [
          94,
          3
        ],
        "size": [
          139,
          134
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_sha_wujing_material/head.png",
        "pos": "headEye",
        "eyeOffset": [
          -9.4,
          -81.12
        ],
        "axisOffset": -7.539
      },
      "right_elbow": {
        "bone": "upperR",
        "pivot": [
          36,
          5
        ],
        "size": [
          79,
          181
        ],
        "draw": 4,
        "mirror": false,
        "img": "../new_sha_wujing_material/right_elbow.png"
      },
      "right_wrist": {
        "bone": "foreR",
        "pivot": [
          91,
          5
        ],
        "size": [
          123,
          182
        ],
        "draw": 4,
        "mirror": true,
        "img": "../new_sha_wujing_material/right_wrist.png"
      },
      "left_elbow": {
        "bone": "upperL",
        "pivot": [
          45,
          6
        ],
        "size": [
          84,
          178
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_sha_wujing_material/left_elbow.png"
      },
      "left_wrist": {
        "bone": "foreL",
        "pivot": [
          47,
          7
        ],
        "size": [
          86,
          190
        ],
        "draw": 2,
        "mirror": false,
        "img": "../new_sha_wujing_material/left_wrist.png"
      },
      "right_hip": {
        "bone": "thighR",
        "pivot": [
          37,
          52
        ],
        "size": [
          95,
          192
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_sha_wujing_material/right_hip.png"
      },
      "right_knee": {
        "bone": "shinR",
        "pivot": [
          50,
          7
        ],
        "size": [
          102,
          173
        ],
        "draw": 3,
        "mirror": true,
        "img": "../new_sha_wujing_material/right_knee.png"
      },
      "left_hip": {
        "bone": "thighL",
        "pivot": [
          54,
          50
        ],
        "size": [
          96,
          190
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_sha_wujing_material/left_hip.png"
      },
      "left_knee": {
        "bone": "shinL",
        "pivot": [
          51,
          7
        ],
        "size": [
          102,
          173
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_sha_wujing_material/left_knee.png"
      }
    },
    "bones": {
      "upperR": {
        "base": "shoulder",
        "len": 107.193,
        "phi": -9.02,
        "part": "right_elbow",
        "axisOffset": 0.217
      },
      "foreR": {
        "base": "elbowR",
        "len": 81.622,
        "phi": -14.263,
        "part": "right_wrist",
        "axisOffset": 350.95
      },
      "upperL": {
        "base": "shoulder",
        "len": 123.595,
        "phi": 45.645,
        "part": "left_elbow",
        "axisOffset": 371.058
      },
      "foreL": {
        "base": "elbowL",
        "len": 88.379,
        "phi": -17.295,
        "part": "left_wrist",
        "axisOffset": 285.768
      },
      "thighR": {
        "base": "hipR",
        "len": 129.977,
        "phi": -16.751,
        "part": "right_hip",
        "axisOffset": -9.599
      },
      "shinR": {
        "base": "kneeR",
        "len": 113.402,
        "phi": 8.965,
        "part": "right_knee",
        "axisOffset": 13.74
      },
      "thighL": {
        "base": "hipL",
        "len": 115.746,
        "phi": -14.105,
        "part": "left_hip",
        "axisOffset": 335.543
      },
      "shinL": {
        "base": "kneeL",
        "len": 104.993,
        "phi": 22.789,
        "part": "left_knee",
        "axisOffset": 382.695
      }
    },
    "limits": {
      "torso": [
        -25,
        25
      ],
      "head": [
        -10,
        10
      ],
      "shoulder": [
        -40,
        155
      ],
      "elbow": [
        0,
        150
      ],
      "hip": [
        -35,
        130
      ],
      "knee": [
        0,
        140
      ]
    },
    "hinge": {
      "elbowR": 1,
      "elbowL": 1,
      "kneeR": -1,
      "kneeL": -1
    },
    "bonesFit": {
      "upperR": {
        "base": "shoulder",
        "len": 110.551,
        "phi": -7.479,
        "part": "right_elbow",
        "axisOffset": 1.758
      },
      "foreR": {
        "base": "elbowR",
        "len": 52.956,
        "phi": -40.242,
        "part": "right_wrist",
        "axisOffset": 324.971
      },
      "upperL": {
        "base": "shoulder",
        "len": 102.184,
        "phi": 53.391,
        "part": "left_elbow",
        "axisOffset": 378.804
      },
      "foreL": {
        "base": "elbowL",
        "len": 72.258,
        "phi": -46.77,
        "part": "left_wrist",
        "axisOffset": 256.293
      },
      "thighR": {
        "base": "hipR",
        "len": 118.275,
        "phi": -2.819,
        "part": "right_hip",
        "axisOffset": 4.334
      },
      "shinR": {
        "base": "kneeR",
        "len": 103.309,
        "phi": -16.939,
        "part": "right_knee",
        "axisOffset": -12.164
      },
      "thighL": {
        "base": "hipL",
        "len": 121.618,
        "phi": 5.205,
        "part": "left_hip",
        "axisOffset": 354.853
      },
      "shinL": {
        "base": "kneeL",
        "len": 102.355,
        "phi": -15.15,
        "part": "left_knee",
        "axisOffset": 344.756
      }
    },
    "skeleton": "unified"
  },
  "tang_sanzang_material": {
    "name": "唐三藏",
    "folder": "tang_sanzang_material",
    "scale": 0.79,
    "pelvis": [
      0.0,
      0.0
    ],
    "neck": [
      18.44,
      -195.67
    ],
    "shoulder": [
      31.21,
      -166.11
    ],
    "shoulderEyes": {
      "right": [
        31.21,
        -166.11
      ],
      "left": [
        31.21,
        -166.11
      ]
    },
    "hips": {
      "left": [
        20.26,
        33.83
      ],
      "right": [
        16.61,
        33.83
      ]
    },
    "phiTorso": -5.384,
    "parts": {
      "body": {
        "pivot": [
          47,
          4
        ],
        "size": [
          90,
          243
        ],
        "draw": 3,
        "mirror": false,
        "img": "../tang_sanzang_material/body.png",
        "pos": "neck",
        "axisOffset": -5.38
      },
      "head": {
        "pivot": [
          50,
          1
        ],
        "size": [
          96,
          105
        ],
        "draw": 2,
        "mirror": false,
        "img": "../tang_sanzang_material/head.png",
        "pos": "headEye",
        "eyeOffset": [
          6.69,
          -96.45
        ],
        "axisOffset": -5.38
      },
      "right_elbow": {
        "bone": "upperR",
        "pivot": [
          29,
          2
        ],
        "size": [
          67,
          118
        ],
        "draw": 4,
        "mirror": false,
        "img": "../tang_sanzang_material/right_elbow.png"
      },
      "right_wrist": {
        "bone": "foreR",
        "pivot": [
          113,
          58
        ],
        "size": [
          147,
          86
        ],
        "draw": 4,
        "mirror": false,
        "img": "../tang_sanzang_material/right_wrist.png"
      },
      "left_elbow": {
        "bone": "upperL",
        "pivot": [
          29,
          2
        ],
        "size": [
          67,
          118
        ],
        "draw": 1,
        "mirror": false,
        "img": "../tang_sanzang_material/left_elbow.png"
      },
      "left_wrist": {
        "bone": "foreL",
        "pivot": [
          113,
          58
        ],
        "size": [
          147,
          86
        ],
        "draw": 2,
        "mirror": false,
        "img": "../tang_sanzang_material/left_wrist.png"
      },
      "right_hip": {
        "bone": "thighR",
        "pivot": [
          71,
          4
        ],
        "size": [
          73,
          116
        ],
        "draw": 1,
        "mirror": false,
        "img": "../tang_sanzang_material/right_hip.png"
      },
      "right_knee": {
        "bone": "shinR",
        "pivot": [
          30,
          4
        ],
        "size": [
          49,
          67
        ],
        "draw": 3,
        "mirror": false,
        "img": "../tang_sanzang_material/right_knee.png"
      },
      "left_hip": {
        "bone": "thighL",
        "pivot": [
          1,
          4
        ],
        "size": [
          73,
          116
        ],
        "draw": 1,
        "mirror": false,
        "img": "../tang_sanzang_material/left_hip.png"
      },
      "left_knee": {
        "bone": "shinL",
        "pivot": [
          18,
          4
        ],
        "size": [
          49,
          67
        ],
        "draw": 3,
        "mirror": false,
        "img": "../tang_sanzang_material/left_knee.png"
      }
    },
    "bones": {
      "upperR": {
        "base": "shoulder",
        "len": 107.193,
        "phi": -9.02,
        "part": "right_elbow",
        "axisOffset": -9.023
      },
      "foreR": {
        "base": "elbowR",
        "len": 81.622,
        "phi": -14.263,
        "part": "right_wrist",
        "axisOffset": 345.74
      },
      "upperL": {
        "base": "shoulder",
        "len": 123.595,
        "phi": 45.645,
        "part": "left_elbow",
        "axisOffset": 405.648
      },
      "foreL": {
        "base": "elbowL",
        "len": 88.379,
        "phi": -17.295,
        "part": "left_wrist",
        "axisOffset": 342.708
      },
      "thighR": {
        "base": "hipR",
        "len": 129.977,
        "phi": -16.751,
        "part": "right_hip",
        "axisOffset": -16.749
      },
      "shinR": {
        "base": "kneeR",
        "len": 113.402,
        "phi": 8.965,
        "part": "right_knee",
        "axisOffset": 8.96
      },
      "thighL": {
        "base": "hipL",
        "len": 115.746,
        "phi": -14.105,
        "part": "left_hip",
        "axisOffset": 345.893
      },
      "shinL": {
        "base": "kneeL",
        "len": 104.993,
        "phi": 22.789,
        "part": "left_knee",
        "axisOffset": 382.785
      }
    },
    "limits": {
      "torso": [
        -25,
        25
      ],
      "head": [
        -10,
        10
      ],
      "shoulder": [
        -40,
        155
      ],
      "elbow": [
        0,
        150
      ],
      "hip": [
        -35,
        130
      ],
      "knee": [
        0,
        140
      ]
    },
    "hinge": {
      "elbowR": 1,
      "elbowL": 1,
      "kneeR": -1,
      "kneeL": -1
    },
    "bonesFit": {
      "upperR": {
        "base": "shoulder",
        "len": 112.281,
        "phi": -8.095,
        "part": "right_elbow",
        "axisOffset": -8.098
      },
      "foreR": {
        "base": "elbowR",
        "len": 60.516,
        "phi": -40.182,
        "part": "right_wrist",
        "axisOffset": 319.821
      },
      "upperL": {
        "base": "shoulder",
        "len": 112.281,
        "phi": -8.095,
        "part": "left_elbow",
        "axisOffset": 351.908
      },
      "foreL": {
        "base": "elbowL",
        "len": 83.826,
        "phi": 58.408,
        "part": "left_wrist",
        "axisOffset": 418.411
      },
      "thighR": {
        "base": "hipR",
        "len": 75.974,
        "phi": -21.801,
        "part": "right_hip",
        "axisOffset": -21.799
      },
      "shinR": {
        "base": "kneeR",
        "len": 116.196,
        "phi": -15.655,
        "part": "right_knee",
        "axisOffset": -15.66
      },
      "thighL": {
        "base": "hipL",
        "len": 75.974,
        "phi": 21.801,
        "part": "left_hip",
        "axisOffset": 381.799
      },
      "shinL": {
        "base": "kneeL",
        "len": 112.97,
        "phi": -6.532,
        "part": "left_knee",
        "axisOffset": 353.464
      }
    },
    "skeleton": "unified"
  },
  "new_zhu_bajie_material": {
    "name": "新猪八戒",
    "folder": "new_zhu_bajie_material",
    "scale": 0.79,
    "pelvis": [
      0.0,
      0.0
    ],
    "neck": [
      18.44,
      -195.67
    ],
    "shoulder": [
      25.67,
      -153.1
    ],
    "shoulderEyes": {
      "right": [
        25.67,
        -153.1
      ],
      "left": [
        25.67,
        -153.1
      ]
    },
    "hips": {
      "left": [
        31.87,
        16.34
      ],
      "right": [
        5.01,
        16.34
      ]
    },
    "phiTorso": -5.384,
    "parts": {
      "body": {
        "pivot": [
          81,
          2
        ],
        "size": [
          154,
          243
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_zhu_bajie_material/body.png",
        "pos": "neck",
        "axisOffset": -5.38
      },
      "head": {
        "pivot": [
          59,
          2
        ],
        "size": [
          126,
          99
        ],
        "draw": 2,
        "mirror": false,
        "img": "../new_zhu_bajie_material/head.png",
        "pos": "headEye",
        "eyeOffset": [
          11.36,
          -85.03
        ],
        "axisOffset": -5.38
      },
      "right_elbow": {
        "bone": "upperR",
        "pivot": [
          94,
          2
        ],
        "size": [
          106,
          83
        ],
        "draw": 4,
        "mirror": false,
        "img": "../new_zhu_bajie_material/right_elbow.png"
      },
      "right_wrist": {
        "bone": "foreR",
        "pivot": [
          96,
          86
        ],
        "size": [
          101,
          106
        ],
        "draw": 4,
        "mirror": false,
        "img": "../new_zhu_bajie_material/right_wrist.png"
      },
      "left_elbow": {
        "bone": "upperL",
        "pivot": [
          94,
          2
        ],
        "size": [
          106,
          83
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_zhu_bajie_material/left_elbow.png"
      },
      "left_wrist": {
        "bone": "foreL",
        "pivot": [
          96,
          86
        ],
        "size": [
          101,
          106
        ],
        "draw": 2,
        "mirror": false,
        "img": "../new_zhu_bajie_material/left_wrist.png"
      },
      "right_hip": {
        "bone": "thighR",
        "pivot": [
          71,
          1
        ],
        "size": [
          83,
          84
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_zhu_bajie_material/right_hip.png"
      },
      "right_knee": {
        "bone": "shinR",
        "pivot": [
          25,
          5
        ],
        "size": [
          93,
          109
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_zhu_bajie_material/right_knee.png"
      },
      "left_hip": {
        "bone": "thighL",
        "pivot": [
          11,
          1
        ],
        "size": [
          83,
          84
        ],
        "draw": 1,
        "mirror": false,
        "img": "../new_zhu_bajie_material/left_hip.png"
      },
      "left_knee": {
        "bone": "shinL",
        "pivot": [
          67,
          5
        ],
        "size": [
          93,
          109
        ],
        "draw": 3,
        "mirror": false,
        "img": "../new_zhu_bajie_material/left_knee.png"
      }
    },
    "bones": {
      "upperR": {
        "base": "shoulder",
        "len": 107.193,
        "phi": -9.02,
        "part": "right_elbow",
        "axisOffset": -9.023
      },
      "foreR": {
        "base": "elbowR",
        "len": 81.622,
        "phi": -14.263,
        "part": "right_wrist",
        "axisOffset": 345.74
      },
      "upperL": {
        "base": "shoulder",
        "len": 123.595,
        "phi": 45.645,
        "part": "left_elbow",
        "axisOffset": 405.648
      },
      "foreL": {
        "base": "elbowL",
        "len": 88.379,
        "phi": -17.295,
        "part": "left_wrist",
        "axisOffset": 342.708
      },
      "thighR": {
        "base": "hipR",
        "len": 129.977,
        "phi": -16.751,
        "part": "right_hip",
        "axisOffset": -16.749
      },
      "shinR": {
        "base": "kneeR",
        "len": 113.402,
        "phi": 8.965,
        "part": "right_knee",
        "axisOffset": 8.96
      },
      "thighL": {
        "base": "hipL",
        "len": 115.746,
        "phi": -14.105,
        "part": "left_hip",
        "axisOffset": 345.893
      },
      "shinL": {
        "base": "kneeL",
        "len": 104.993,
        "phi": 22.789,
        "part": "left_knee",
        "axisOffset": 382.785
      }
    },
    "limits": {
      "torso": [
        -25,
        25
      ],
      "head": [
        -10,
        10
      ],
      "shoulder": [
        -40,
        155
      ],
      "elbow": [
        0,
        150
      ],
      "hip": [
        -35,
        130
      ],
      "knee": [
        0,
        140
      ]
    },
    "hinge": {
      "elbowR": 1,
      "elbowL": 1,
      "kneeR": -1,
      "kneeL": -1
    },
    "bonesFit": {
      "upperR": {
        "base": "shoulder",
        "len": 86.456,
        "phi": -49.362,
        "part": "right_elbow",
        "axisOffset": -49.365
      },
      "foreR": {
        "base": "elbowR",
        "len": 89.57,
        "phi": 10.477,
        "part": "right_wrist",
        "axisOffset": 370.48
      },
      "upperL": {
        "base": "shoulder",
        "len": 86.456,
        "phi": -49.362,
        "part": "left_elbow",
        "axisOffset": 310.641
      },
      "foreL": {
        "base": "elbowL",
        "len": 153.023,
        "phi": 55.916,
        "part": "left_wrist",
        "axisOffset": 415.919
      },
      "thighR": {
        "base": "hipR",
        "len": 69.846,
        "phi": 53.723,
        "part": "right_hip",
        "axisOffset": 53.726
      },
      "shinR": {
        "base": "kneeR",
        "len": 189.802,
        "phi": -33.324,
        "part": "right_knee",
        "axisOffset": -33.329
      },
      "thighL": {
        "base": "hipL",
        "len": 69.846,
        "phi": -53.723,
        "part": "left_hip",
        "axisOffset": 306.275
      },
      "shinL": {
        "base": "kneeL",
        "len": 169.916,
        "phi": 20.701,
        "part": "left_knee",
        "axisOffset": 380.698
      }
    },
    "skeleton": "unified"
  }
};
