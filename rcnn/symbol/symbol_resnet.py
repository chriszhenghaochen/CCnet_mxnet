# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import mxnet as mx
from rcnn.config import config
from . import proposal
from . import proposal_target

eps = 2e-5
use_global_stats = True
workspace = 512
res_deps = {'50': (3, 4, 6, 3), '101': (3, 4, 23, 3), '152': (3, 8, 36, 3), '200': (3, 24, 36, 3)}
units = res_deps['101']
filter_list = [256, 512, 1024, 2048]
score_scale = config.CCNET.score_scale

def residual_unit(data, num_filter, stride, dim_match, name):
    bn1 = mx.sym.BatchNorm(data=data, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name=name + '_bn1')
    act1 = mx.sym.Activation(data=bn1, act_type='relu', name=name + '_relu1')
    conv1 = mx.sym.Convolution(data=act1, num_filter=int(num_filter * 0.25), kernel=(1, 1), stride=(1, 1), pad=(0, 0),
                               no_bias=True, workspace=workspace, name=name + '_conv1')
    bn2 = mx.sym.BatchNorm(data=conv1, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name=name + '_bn2')
    act2 = mx.sym.Activation(data=bn2, act_type='relu', name=name + '_relu2')
    conv2 = mx.sym.Convolution(data=act2, num_filter=int(num_filter * 0.25), kernel=(3, 3), stride=stride, pad=(1, 1),
                               no_bias=True, workspace=workspace, name=name + '_conv2')
    bn3 = mx.sym.BatchNorm(data=conv2, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name=name + '_bn3')
    act3 = mx.sym.Activation(data=bn3, act_type='relu', name=name + '_relu3')
    conv3 = mx.sym.Convolution(data=act3, num_filter=num_filter, kernel=(1, 1), stride=(1, 1), pad=(0, 0), no_bias=True,
                               workspace=workspace, name=name + '_conv3')
    if dim_match:
        shortcut = data
    else:
        shortcut = mx.sym.Convolution(data=act1, num_filter=num_filter, kernel=(1, 1), stride=stride, no_bias=True,
                                      workspace=workspace, name=name + '_sc')
    sum = mx.sym.ElementWiseSum(*[conv3, shortcut], name=name + '_plus')
    return sum


def get_resnet_conv(data):
    # res1
    data_bn = mx.sym.BatchNorm(data=data, fix_gamma=True, eps=eps, use_global_stats=use_global_stats, name='bn_data')
    conv0 = mx.sym.Convolution(data=data_bn, num_filter=64, kernel=(7, 7), stride=(2, 2), pad=(3, 3),
                               no_bias=True, name="conv0", workspace=workspace)
    bn0 = mx.sym.BatchNorm(data=conv0, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn0')
    relu0 = mx.sym.Activation(data=bn0, act_type='relu', name='relu0')
    pool0 = mx.symbol.Pooling(data=relu0, kernel=(3, 3), stride=(2, 2), pad=(1, 1), pool_type='max', name='pool0')

    # res2
    unit = residual_unit(data=pool0, num_filter=filter_list[0], stride=(1, 1), dim_match=False, name='stage1_unit1')
    for i in range(2, units[0] + 1):
        unit = residual_unit(data=unit, num_filter=filter_list[0], stride=(1, 1), dim_match=True, name='stage1_unit%s' % i)

    # res3
    unit = residual_unit(data=unit, num_filter=filter_list[1], stride=(2, 2), dim_match=False, name='stage2_unit1')
    for i in range(2, units[1] + 1):
        unit = residual_unit(data=unit, num_filter=filter_list[1], stride=(1, 1), dim_match=True, name='stage2_unit%s' % i)

    # res4
    unit = residual_unit(data=unit, num_filter=filter_list[2], stride=(2, 2), dim_match=False, name='stage3_unit1')
    for i in range(2, units[2] + 1):
        unit = residual_unit(data=unit, num_filter=filter_list[2], stride=(1, 1), dim_match=True, name='stage3_unit%s' % i)
    return unit


def get_resnet_train(num_classes=config.NUM_CLASSES, num_anchors=config.NUM_ANCHORS):
    data = mx.symbol.Variable(name="data")
    im_info = mx.symbol.Variable(name="im_info")
    gt_boxes = mx.symbol.Variable(name="gt_boxes")
    rpn_label = mx.symbol.Variable(name='label')
    rpn_bbox_target = mx.symbol.Variable(name='bbox_target')
    rpn_bbox_weight = mx.symbol.Variable(name='bbox_weight')

    # shared convolutional layers
    conv_feat = get_resnet_conv(data)

    # RPN layers
    rpn_conv = mx.symbol.Convolution(
        data=conv_feat, kernel=(3, 3), pad=(1, 1), num_filter=512, name="rpn_conv_3x3")
    rpn_relu = mx.symbol.Activation(data=rpn_conv, act_type="relu", name="rpn_relu")
    rpn_cls_score = mx.symbol.Convolution(
        data=rpn_relu, kernel=(1, 1), pad=(0, 0), num_filter=2 * num_anchors, name="rpn_cls_score")
    rpn_bbox_pred = mx.symbol.Convolution(
        data=rpn_relu, kernel=(1, 1), pad=(0, 0), num_filter=4 * num_anchors, name="rpn_bbox_pred")

    # prepare rpn data
    rpn_cls_score_reshape = mx.symbol.Reshape(
        data=rpn_cls_score, shape=(0, 2, -1, 0), name="rpn_cls_score_reshape")

    # classification
    rpn_cls_prob = mx.symbol.SoftmaxOutput(data=rpn_cls_score_reshape, label=rpn_label, multi_output=True,
                                           normalization='valid', use_ignore=True, ignore_label=-1, name="rpn_cls_prob")
    # bounding box regression
    rpn_bbox_loss_ = rpn_bbox_weight * mx.symbol.smooth_l1(name='rpn_bbox_loss_', scalar=3.0, data=(rpn_bbox_pred - rpn_bbox_target))
    rpn_bbox_loss = mx.sym.MakeLoss(name='rpn_bbox_loss', data=rpn_bbox_loss_, grad_scale=1.0 / config.TRAIN.RPN_BATCH_SIZE)

    # ROI proposal
    rpn_cls_act = mx.symbol.SoftmaxActivation(
        data=rpn_cls_score_reshape, mode="channel", name="rpn_cls_act")
    rpn_cls_act_reshape = mx.symbol.Reshape(
        data=rpn_cls_act, shape=(0, 2 * num_anchors, -1, 0), name='rpn_cls_act_reshape')
    if config.TRAIN.CXX_PROPOSAL:
        rois = mx.symbol.contrib.Proposal(
            cls_prob=rpn_cls_act_reshape, bbox_pred=rpn_bbox_pred, im_info=im_info, name='rois',
            feature_stride=config.RPN_FEAT_STRIDE, scales=tuple(config.ANCHOR_SCALES), ratios=tuple(config.ANCHOR_RATIOS),
            rpn_pre_nms_top_n=config.TRAIN.RPN_PRE_NMS_TOP_N, rpn_post_nms_top_n=config.TRAIN.RPN_POST_NMS_TOP_N,
            threshold=config.TRAIN.RPN_NMS_THRESH, rpn_min_size=config.TRAIN.RPN_MIN_SIZE)
    else:
        rois = mx.symbol.Custom(
            cls_prob=rpn_cls_act_reshape, bbox_pred=rpn_bbox_pred, im_info=im_info, name='rois',
            op_type='proposal', feat_stride=config.RPN_FEAT_STRIDE,
            scales=tuple(config.ANCHOR_SCALES), ratios=tuple(config.ANCHOR_RATIOS),
            rpn_pre_nms_top_n=config.TRAIN.RPN_PRE_NMS_TOP_N, rpn_post_nms_top_n=config.TRAIN.RPN_POST_NMS_TOP_N,
            threshold=config.TRAIN.RPN_NMS_THRESH, rpn_min_size=config.TRAIN.RPN_MIN_SIZE)

    # ROI proposal target
    gt_boxes_reshape = mx.symbol.Reshape(data=gt_boxes, shape=(-1, 5), name='gt_boxes_reshape')
    group = mx.symbol.Custom(rois=rois, gt_boxes=gt_boxes_reshape, op_type='proposal_target',
                             num_classes=num_classes, batch_images=config.TRAIN.BATCH_IMAGES,
                             batch_rois=config.TRAIN.BATCH_ROIS, fg_fraction=config.TRAIN.FG_FRACTION)
    rois = group[0]
    label = group[1]
    bbox_target = group[2]
    bbox_weight = group[3]

    # Fast R-CNN
    # ROI pooling
    roi_pool = mx.symbol.ROIPooling(
        name='roi_pool5', data=conv_feat, rois=rois, pooled_size=(14, 14), spatial_scale=1.0 / config.RCNN_FEAT_STRIDE)


    #--------------------------------------------------------CCNET START-----------------------------------------------------#
    #feature 5_1
    bn_roi_pool5_1 = mx.sym.BatchNorm(data=roi_pool, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_1')
    relu_pool5_1 = mx.sym.Activation(data=bn_roi_pool5_1, act_type='relu', name='relu_pool5_1')
    pool5_1 = mx.symbol.Pooling(data=relu_pool5_1, global_pool=True, kernel=(14, 14), pool_type='avg', name='pool5_1')
    pool_5_1_scale = pool5_1*1.0
    cls_score1 = mx.symbol.FullyConnected(name='cls_score1', data=pool_5_1_scale, num_hidden=num_classes)
    cls_prob1 = mx.symbol.SoftmaxOutput(name='cls_prob1', data=cls_score1, label=label, normalization='batch')
    #feature 5_1 done

    # UNIT5_2 resize conv
    unit = residual_unit(data=roi_pool, num_filter=filter_list[3], stride=(2, 2), dim_match=False, name='stage4_unit1')
    # resize conv done

    #feature 5_2
    bn_roi_pool5_2 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_2')
    relu_pool5_2 = mx.sym.Activation(data=bn_roi_pool5_2, act_type='relu', name='relu_pool5_2')
    pool5_2 = mx.symbol.Pooling(data=relu_pool5_2, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool5_2')
    pool_5_2_scale = pool5_2*1.0
    pool_5_2_concat = mx.symbol.Concat(pool_5_2_scale, pool_5_1_scale, dim = 1, name = 'pool_5_2_concat')
    cls_score2 = mx.symbol.FullyConnected(name='cls_score2', data=pool_5_2_concat, num_hidden=num_classes)
    cls_score2 = cls_score2 + cls_score1*score_scale
    cls_prob2 = mx.symbol.SoftmaxOutput(name='cls_prob2', data=cls_score2, label=label, normalization='batch')
    #feature 5_2 done

    #UNIT5_3 conv
    unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit2')

    #feature 5_3 
    bn_roi_pool5_3 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_3')
    relu_pool5_3 = mx.sym.Activation(data=bn_roi_pool5_3, act_type='relu', name='relu_pool5_3')
    pool5_3 = mx.symbol.Pooling(data=relu_pool5_3, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool5_3')
    pool_5_3_scale = pool5_3*1.0
    pool_5_3_concat = mx.symbol.Concat(pool_5_3_scale, pool_5_2_concat, dim = 1, name = 'pool_5_3_concat')
    cls_score3 = mx.symbol.FullyConnected(name='cls_score3', data=pool_5_3_concat, num_hidden=num_classes)
    cls_score3 = cls_score3 + cls_score2*score_scale
    cls_prob3 = mx.symbol.SoftmaxOutput(name='cls_prob3', data=cls_score3, label=label, normalization='batch')
    #feature 5_3 done

    #UNIT5_4 conv
    unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit3')
    # #--------------------------------------------------------CCNET DONE-----------------------------------------------------#


    # #original FRCN code
    # for i in range(2, units[3] + 1):
    #     unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit%s' % i)
    # #original code done

    bn1 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn1')
    relu1 = mx.sym.Activation(data=bn1, act_type='relu', name='relu1')
    pool1 = mx.symbol.Pooling(data=relu1, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool1')
    pool_concat = mx.symbol.Concat(pool1, pool_5_3_concat, dim = 1, name = 'pool1_concat')

    # classfication
    cls_score = mx.symbol.FullyConnected(name='cls_score', data=pool_concat, num_hidden=num_classes)
    cls_score = cls_score + cls_score3*score_scale
    cls_prob = mx.symbol.SoftmaxOutput(name='cls_prob', data=cls_score, label=label, normalization='batch')

    # bounding box regression
    bbox_pred = mx.symbol.FullyConnected(name='bbox_pred', data=pool_concat, num_hidden=num_classes * 4)
    bbox_loss_ = bbox_weight * mx.symbol.smooth_l1(name='bbox_loss_', scalar=1.0, data=(bbox_pred - bbox_target))
    bbox_loss = mx.sym.MakeLoss(name='bbox_loss', data=bbox_loss_, grad_scale=1.0 / config.TRAIN.BATCH_ROIS)

    # reshape output
    label = mx.symbol.Reshape(data=label, shape=(config.TRAIN.BATCH_IMAGES, -1), name='label_reshape')
    cls_prob = mx.symbol.Reshape(data=cls_prob, shape=(config.TRAIN.BATCH_IMAGES, -1, num_classes), name='cls_prob_reshape')
    
    #new classifier
    cls_prob1 = mx.symbol.Reshape(data=cls_prob1, shape=(config.TRAIN.BATCH_IMAGES, -1, num_classes), name='cls_prob_reshape1')
    cls_prob2 = mx.symbol.Reshape(data=cls_prob2, shape=(config.TRAIN.BATCH_IMAGES, -1, num_classes), name='cls_prob_reshape2')
    cls_prob3 = mx.symbol.Reshape(data=cls_prob3, shape=(config.TRAIN.BATCH_IMAGES, -1, num_classes), name='cls_prob_reshape3')
    #new classifier done

    bbox_loss = mx.symbol.Reshape(data=bbox_loss, shape=(config.TRAIN.BATCH_IMAGES, -1, 4 * num_classes), name='bbox_loss_reshape')

    # group = mx.symbol.Group([rpn_cls_prob, rpn_bbox_loss, cls_prob, bbox_loss, mx.symbol.BlockGrad(label)])
    group = mx.symbol.Group([rpn_cls_prob, rpn_bbox_loss, cls_prob1, cls_prob2, cls_prob3, cls_prob, bbox_loss, mx.symbol.BlockGrad(label)])
    return group


def get_resnet_test(num_classes=config.NUM_CLASSES, num_anchors=config.NUM_ANCHORS):
    data = mx.symbol.Variable(name="data")
    im_info = mx.symbol.Variable(name="im_info")

    # shared convolutional layers
    conv_feat = get_resnet_conv(data)

    # RPN
    rpn_conv = mx.symbol.Convolution(
        data=conv_feat, kernel=(3, 3), pad=(1, 1), num_filter=512, name="rpn_conv_3x3")
    rpn_relu = mx.symbol.Activation(data=rpn_conv, act_type="relu", name="rpn_relu")
    rpn_cls_score = mx.symbol.Convolution(
        data=rpn_relu, kernel=(1, 1), pad=(0, 0), num_filter=2 * num_anchors, name="rpn_cls_score")
    rpn_bbox_pred = mx.symbol.Convolution(
        data=rpn_relu, kernel=(1, 1), pad=(0, 0), num_filter=4 * num_anchors, name="rpn_bbox_pred")

    # ROI Proposal
    rpn_cls_score_reshape = mx.symbol.Reshape(
        data=rpn_cls_score, shape=(0, 2, -1, 0), name="rpn_cls_score_reshape")
    rpn_cls_prob = mx.symbol.SoftmaxActivation(
        data=rpn_cls_score_reshape, mode="channel", name="rpn_cls_prob")
    rpn_cls_prob_reshape = mx.symbol.Reshape(
        data=rpn_cls_prob, shape=(0, 2 * num_anchors, -1, 0), name='rpn_cls_prob_reshape')
    if config.TEST.CXX_PROPOSAL:
        rois = mx.symbol.contrib.Proposal(
            cls_prob=rpn_cls_prob_reshape, bbox_pred=rpn_bbox_pred, im_info=im_info, name='rois',
            feature_stride=config.RPN_FEAT_STRIDE, scales=tuple(config.ANCHOR_SCALES), ratios=tuple(config.ANCHOR_RATIOS),
            rpn_pre_nms_top_n=config.TEST.RPN_PRE_NMS_TOP_N, rpn_post_nms_top_n=config.TEST.RPN_POST_NMS_TOP_N,
            threshold=config.TEST.RPN_NMS_THRESH, rpn_min_size=config.TEST.RPN_MIN_SIZE)
    else:
        rois = mx.symbol.Custom(
            cls_prob=rpn_cls_prob_reshape, bbox_pred=rpn_bbox_pred, im_info=im_info, name='rois',
            op_type='proposal', feat_stride=config.RPN_FEAT_STRIDE,
            scales=tuple(config.ANCHOR_SCALES), ratios=tuple(config.ANCHOR_RATIOS),
            rpn_pre_nms_top_n=config.TEST.RPN_PRE_NMS_TOP_N, rpn_post_nms_top_n=config.TEST.RPN_POST_NMS_TOP_N,
            threshold=config.TEST.RPN_NMS_THRESH, rpn_min_size=config.TEST.RPN_MIN_SIZE)

    # Fast R-CNN
    # ROI pooling
    roi_pool = mx.symbol.ROIPooling(
        name='roi_pool5', data=conv_feat, rois=rois, pooled_size=(14, 14), spatial_scale=1.0 / config.RCNN_FEAT_STRIDE)

    #--------------------------------------------------------CCNET START-----------------------------------------------------#
    bn_roi_pool5_1 = mx.sym.BatchNorm(data=roi_pool, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_1')
    relu_pool5_1 = mx.sym.Activation(data=bn_roi_pool5_1, act_type='relu', name='relu_pool5_1')
    pool5_1 = mx.symbol.Pooling(data=relu_pool5_1, global_pool=True, kernel=(14, 14), pool_type='avg', name='pool5_1')
    pool_5_1_scale = pool5_1*1.0
    cls_score1 = mx.symbol.FullyConnected(name='cls_score1', data=pool_5_1_scale, num_hidden=num_classes)
    #feature 5_1 done


    # UNIT5_2 resize conv
    unit = residual_unit(data=roi_pool, num_filter=filter_list[3], stride=(2, 2), dim_match=False, name='stage4_unit1')
    # resize conv done

    # #feature 5_2
    bn_roi_pool5_2 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_2')
    relu_pool5_2 = mx.sym.Activation(data=bn_roi_pool5_2, act_type='relu', name='relu_pool5_2')
    pool5_2 = mx.symbol.Pooling(data=relu_pool5_2, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool5_2')
    pool_5_2_scale = pool5_2*1.0
    pool_5_2_concat = mx.symbol.Concat(pool_5_2_scale, pool_5_1_scale, dim = 1, name = 'pool_5_2_concat')
    cls_score2 = mx.symbol.FullyConnected(name='cls_score2', data=pool_5_2_concat, num_hidden=num_classes)
    cls_score2 = cls_score2 + cls_score1*score_scale
    #feature 5_2 done

    #UNIT5_3 conv
    unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit2')

    #feature 5_3 
    bn_roi_pool5_3 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn_roi_pool5_3')
    relu_pool5_3 = mx.sym.Activation(data=bn_roi_pool5_3, act_type='relu', name='relu_pool5_3')
    pool5_3 = mx.symbol.Pooling(data=relu_pool5_3, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool5_3')
    pool_5_3_scale = pool5_3*1.0
    pool_5_3_concat = mx.symbol.Concat(pool_5_3_scale, pool_5_2_concat, dim = 1, name = 'pool_5_3_concat')
    cls_score3 = mx.symbol.FullyConnected(name='cls_score3', data=pool_5_3_concat, num_hidden=num_classes)
    cls_score3 = cls_score3 + cls_score2*score_scale    
    #feature 5_3 done

    #UNIT5_4 conv
    unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit3')
    #--------------------------------------------------------CCNET DONE-----------------------------------------------------#


    # #original FRCN code
    # for i in range(2, units[3] + 1):
    #     unit = residual_unit(data=unit, num_filter=filter_list[3], stride=(1, 1), dim_match=True, name='stage4_unit%s' % i)
    # #original code done

    bn1 = mx.sym.BatchNorm(data=unit, fix_gamma=False, eps=eps, use_global_stats=use_global_stats, name='bn1')
    relu1 = mx.sym.Activation(data=bn1, act_type='relu', name='relu1')
    pool1 = mx.symbol.Pooling(data=relu1, global_pool=True, kernel=(7, 7), pool_type='avg', name='pool1')
    pool_concat = mx.symbol.Concat(pool1, pool_5_3_concat, dim = 1, name = 'pool1_concat')

    # classification
    cls_score = mx.symbol.FullyConnected(name='cls_score', data=pool_concat, num_hidden=num_classes)
    cls_score = cls_score + cls_score3*score_scale
    cls_prob = mx.symbol.softmax(name='cls_prob', data=cls_score)

    # bounding box regression
    bbox_pred = mx.symbol.FullyConnected(name='bbox_pred', data=pool_concat, num_hidden=num_classes * 4)

    # reshape output
    cls_prob = mx.symbol.Reshape(data=cls_prob, shape=(config.TEST.BATCH_IMAGES, -1, num_classes), name='cls_prob_reshape')
    bbox_pred = mx.symbol.Reshape(data=bbox_pred, shape=(config.TEST.BATCH_IMAGES, -1, 4 * num_classes), name='bbox_pred_reshape')

    # group output
    group = mx.symbol.Group([rois, cls_prob, bbox_pred])
    return group
